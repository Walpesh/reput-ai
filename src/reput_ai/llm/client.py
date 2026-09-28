import json
import logging
from typing import Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from reput_ai.config import settings
from reput_ai.db.models.branch import ToneOfVoice
from reput_ai.llm.prompts import build_system_prompt
from reput_ai.llm.sanitizer import format_user_review_payload

logger = logging.getLogger("reput_ai.llm")

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def is_retryable_error(exception: BaseException) -> bool:
    """Retry network failures, timeouts, rate limits and 5xx responses."""
    if isinstance(exception, (httpx.RequestError, httpx.TimeoutException)):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code in RETRYABLE_STATUS_CODES
    return False


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
        max_retries: int = 3,
        retry_min_wait: float = 1.0,
        retry_max_wait: float = 8.0,
    ):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.base_url = (base_url or settings.OPENROUTER_BASE_URL).rstrip("/")
        self.primary_model = primary_model or settings.PRIMARY_LLM_MODEL
        self.fallback_model = fallback_model or settings.FALLBACK_LLM_MODEL
        self.max_retries = max_retries
        self.retry_min_wait = retry_min_wait
        self.retry_max_wait = retry_max_wait

    async def _call_model(
        self,
        client: httpx.AsyncClient,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=self.retry_min_wait, max=self.retry_max_wait),
            retry=retry_if_exception(is_retryable_error),
            reraise=True,
        ):
            with attempt:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://reputation-ai.local",
                    "X-Title": "ReputationAI",
                }
                payload = {
                    "model": model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                }
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        raise RuntimeError("LLM call exhausted retries without a response")

    @staticmethod
    def _parse_reply(raw_content: str) -> str:
        """Parse strict JSON response {"reply": "..."} or fall back to raw text."""
        try:
            parsed = json.loads(raw_content)
            if isinstance(parsed, dict) and "reply" in parsed:
                return str(parsed["reply"])
        except Exception:
            pass
        return raw_content.strip()

    async def generate_reply(
        self,
        review_text: str,
        tone_of_voice: ToneOfVoice = ToneOfVoice.OFFICIAL,
        client: Optional[httpx.AsyncClient] = None,
    ) -> str:
        """Generate a reply with sanitization, tone of voice, retries and model fallback."""
        system_prompt = build_system_prompt(tone_of_voice)
        user_prompt = format_user_review_payload(review_text)

        if client is not None:
            return await self._execute_generation(client, system_prompt, user_prompt)

        async with httpx.AsyncClient() as managed_client:
            return await self._execute_generation(managed_client, system_prompt, user_prompt)

    async def _execute_generation(
        self,
        client: httpx.AsyncClient,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        try:
            logger.info("Generating reply using primary model: %s", self.primary_model)
            raw_response = await self._call_model(client, self.primary_model, system_prompt, user_prompt)
            return self._parse_reply(raw_response)
        except Exception as primary_err:
            logger.warning(
                "Primary model '%s' failed (%s: %s). Switching to fallback model '%s'.",
                self.primary_model,
                type(primary_err).__name__,
                primary_err,
                self.fallback_model,
            )
            try:
                raw_response = await self._call_model(
                    client, self.fallback_model, system_prompt, user_prompt
                )
                return self._parse_reply(raw_response)
            except Exception as fallback_err:
                logger.error(
                    "Fallback model '%s' also failed (%s: %s)",
                    self.fallback_model,
                    type(fallback_err).__name__,
                    fallback_err,
                )
                raise
