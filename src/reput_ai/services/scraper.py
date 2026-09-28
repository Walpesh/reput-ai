"""Yandex Maps review scraper used by the periodic Celery polling tasks.

The scraper talks to the public ``fetchReviews`` endpoint of Yandex Maps, retries
transient network/5xx failures with an exponential backoff (the same policy as the
LLM client) and normalizes the payload into :class:`ScrapedReview` records that the
review pipeline can persist.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from reput_ai.config import settings
from reput_ai.core.parsing import parse_iso_datetime
from reput_ai.db.models.branch import PlatformType

logger = logging.getLogger("reput_ai.services.scraper")

YANDEX_REVIEWS_ENDPOINT = "https://yandex.ru/maps/api/business/fetchReviews"
BUSINESS_ID_PATTERN = re.compile(r"(?:/org/[^/]+/|businessId=|/business/)(\d{5,})")
LOOSE_ID_PATTERN = re.compile(r"(\d{6,})")
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class ScraperError(Exception):
    """Raised when reviews cannot be retrieved from an external platform."""


class ScraperTransientError(ScraperError):
    """Transient scraping failure that is worth retrying."""


@dataclass(frozen=True)
class ScrapedReview:
    """Normalized review coming from an external platform."""

    external_id: str
    author_name: str
    rating: int
    text: str
    published_at: datetime | None = None


class ReviewScraper(Protocol):
    """Interface implemented by every platform scraper."""

    async def fetch_reviews(self, platform_url: str) -> list[ScrapedReview]: ...


def is_retryable_scraper_error(exception: BaseException) -> bool:
    """Retry network failures, timeouts and 429/5xx responses."""
    if isinstance(exception, (httpx.RequestError, httpx.TimeoutException, ScraperTransientError)):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code in RETRYABLE_STATUS_CODES
    return False


class YandexMapsScraper:
    """Collects public reviews of a Yandex Maps organisation page."""

    platform = PlatformType.YANDEX

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        max_reviews: int | None = None,
        max_attempts: int | None = None,
        retry_min_wait: float = 1.0,
        retry_max_wait: float = 8.0,
    ) -> None:
        self._client = http_client
        self.max_reviews = max_reviews or settings.SCRAPER_MAX_REVIEWS_PER_BRANCH
        self.max_attempts = max_attempts or settings.SCRAPER_MAX_ATTEMPTS
        self.retry_min_wait = retry_min_wait
        self.retry_max_wait = retry_max_wait

    # -- parsing helpers ---------------------------------------------------
    @staticmethod
    def extract_business_id(platform_url: str) -> str | None:
        """Extract the Yandex organisation id from a maps URL."""
        match = BUSINESS_ID_PATTERN.search(platform_url)
        if match:
            return match.group(1)

        query = parse_qs(urlparse(platform_url).query)
        for key in ("businessId", "business_id", "oid"):
            if query.get(key):
                return str(query[key][0])

        fallback = LOOSE_ID_PATTERN.search(platform_url)
        return fallback.group(1) if fallback else None

    @staticmethod
    def parse_reviews(payload: dict[str, Any]) -> list[ScrapedReview]:
        """Normalize the ``fetchReviews`` payload into scraped review records."""
        container = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(container, dict):
            raise ScraperError("Unexpected Yandex Maps reviews payload")

        raw_reviews = container.get("reviews")
        if not isinstance(raw_reviews, list):
            raise ScraperError("Yandex Maps reviews payload has no 'reviews' list")

        reviews: list[ScrapedReview] = []
        for raw in raw_reviews:
            review = YandexMapsScraper._parse_single_review(raw)
            if review is not None:
                reviews.append(review)
        return reviews

    @staticmethod
    def _parse_single_review(raw: Any) -> ScrapedReview | None:
        if not isinstance(raw, dict):
            return None

        external_id = raw.get("reviewId") or raw.get("id")
        if not external_id:
            return None

        try:
            rating = int(raw.get("rating") or 0)
        except (TypeError, ValueError):
            return None
        if not 1 <= rating <= 5:
            return None

        text = str(raw.get("text") or "").strip()
        if not text:
            return None

        author = raw.get("author")
        author_name = ""
        if isinstance(author, dict):
            author_name = str(author.get("name") or "")
        author_name = author_name or str(raw.get("authorName") or "Аноним")

        return ScrapedReview(
            external_id=str(external_id),
            author_name=author_name[:255],
            rating=rating,
            text=text,
            published_at=parse_iso_datetime(raw.get("updatedTime") or raw.get("createdTime")),
        )

    # -- network -----------------------------------------------------------
    async def fetch_reviews(self, platform_url: str) -> list[ScrapedReview]:
        """Fetch and normalize the latest reviews of a Yandex Maps organisation page."""
        business_id = self.extract_business_id(platform_url)
        if not business_id:
            raise ScraperError(f"Cannot resolve a Yandex business id from URL: {platform_url}")

        payload = await self._request_reviews(business_id)
        reviews = self.parse_reviews(payload)
        logger.info("Yandex Maps returned %s reviews for business %s", len(reviews), business_id)
        return reviews[: self.max_reviews]

    async def _request_reviews(self, business_id: str) -> dict[str, Any]:
        endpoint = (
            f"{YANDEX_REVIEWS_ENDPOINT}?businessId={business_id}&ranking=by_time&page=1"
        )
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(
                multiplier=1, min=self.retry_min_wait, max=self.retry_max_wait
            ),
            retry=retry_if_exception(is_retryable_scraper_error),
            reraise=True,
        ):
            with attempt:
                return await self._do_request(endpoint)

        raise ScraperError("Yandex Maps request exhausted retries without a response")

    async def _do_request(self, endpoint: str) -> dict[str, Any]:
        headers = {
            "User-Agent": settings.SCRAPER_USER_AGENT,
            "Accept": "application/json",
        }
        timeout = settings.SCRAPER_TIMEOUT_SECONDS

        if self._client is not None:
            response = await self._client.get(endpoint, headers=headers, timeout=timeout)
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(endpoint, headers=headers, timeout=timeout)

        if response.status_code in RETRYABLE_STATUS_CODES:
            raise ScraperTransientError(
                f"Yandex Maps is temporarily unavailable ({response.status_code})"
            )
        if response.status_code >= 400:
            raise ScraperError(
                f"Yandex Maps rejected the request with status {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ScraperError("Yandex Maps returned a non-JSON payload") from exc

        if not isinstance(payload, dict):
            raise ScraperError("Unexpected Yandex Maps payload type")
        return payload


SCRAPER_REGISTRY: dict[PlatformType, type[YandexMapsScraper]] = {
    PlatformType.YANDEX: YandexMapsScraper,
}


def build_scraper(
    platform_type: PlatformType,
    http_client: httpx.AsyncClient | None = None,
) -> ReviewScraper | None:
    """Return a scraper for the platform (``None`` when it is not supported yet)."""
    scraper_cls = SCRAPER_REGISTRY.get(platform_type)
    if scraper_cls is None:
        logger.info("No scraper implemented for platform %s yet", platform_type)
        return None
    return scraper_cls(http_client=http_client)
