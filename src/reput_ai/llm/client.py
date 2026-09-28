import json
import logging
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from reput_ai.config import settings
from reput_ai.db.models.branch import ToneOfVoice
from reput_ai.llm.prompts import build_system_prompt
from reput_ai.llm.sanitizer import format_user_review_payload

logger = logging.getLogger("reput_ai.llm")


class LLMClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.base_url = base_url or settings.OPENROUTER_BASE_URL
        self.primary_model = primary_model or settings.PRIMARY_LLM_MODEL
        self.fallback_model = fallback_model or settings.FALLBACK_LLM_MODEL

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _call_model(self, client: httpx.AsyncClient, model: str, system_prompt: str, user_prompt: str) -> str:
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
        content = data["choices"][0]["message"]["content"]
        return content

    async def generate_reply(
        self,
        review_text: str,
        tone_of_voice: ToneOfVoice = ToneOfVoice.OFFICIAL,
    ) -> str:
        system_prompt = build_system_prompt(tone_of_voice)
        user_prompt = format_user_review_payload(review_text)

        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"Generating reply using primary model: {self.primary_model}")
                raw_response = await self._call_model(client, self.primary_model, system_prompt, user_prompt)
                parsed = json.loads(raw_response)
                return parsed.get("reply", raw_response)
            except Exception as e:
                logger.warning(f"Primary model {self.primary_model} failed: {e}. Switching to fallback {self.fallback_model}")
                try:
                    raw_response = await self._call_model(client, self.fallback_model, system_prompt, user_prompt)
                    parsed = json.loads(raw_response)
                    return parsed.get("reply", raw_response)
                except Exception as fallback_err:
                    logger.error(f"Fallback model failed as well: {fallback_err}")
                    raise fallback_err
