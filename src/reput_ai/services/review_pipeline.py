"""Async review pipeline shared by the Celery workers and the API.

Flow implemented here::

    scrape (Yandex Maps) -> ingest new reviews (status NEW)
        -> AI drafts a reply (status PENDING_APPROVAL) -> owner approves in Telegram

Every function takes an ``async_sessionmaker`` so the workers can hand over their
own session factory and tests can inject an isolated database.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from reput_ai.core.time import ensure_aware, utcnow
from reput_ai.db.models.branch import CompanyBranch, PlatformType, ToneOfVoice
from reput_ai.db.models.review import Review, ReviewStatus
from reput_ai.db.models.subscription import Subscription
from reput_ai.db.models.user import User
from reput_ai.llm.client import LLMClient
from reput_ai.services.billing import BillingService
from reput_ai.services.platforms import get_platform_display_name
from reput_ai.services.scraper import ReviewScraper, ScrapedReview

logger = logging.getLogger("reput_ai.services.review_pipeline")

SessionFactory = async_sessionmaker[AsyncSession]
ScraperFactory = Callable[[PlatformType], ReviewScraper | None]


@dataclass(frozen=True)
class IngestStats:
    """How many reviews were stored / already known for a branch."""

    created: int = 0
    duplicates: int = 0

    @property
    def total(self) -> int:
        return self.created + self.duplicates


@dataclass(frozen=True)
class ScrapeOutcome:
    """Result of one branch polling attempt."""

    status: str
    branch_id: UUID
    created: int = 0
    duplicates: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "branch_id": str(self.branch_id),
            "created": self.created,
            "duplicates": self.duplicates,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class GeneratedReply:
    """Everything the Telegram notifier needs after the LLM produced a draft."""

    review_id: UUID
    generated_reply: str
    review_text: str
    rating: int
    author_name: str
    branch_name: str
    platform_name: str
    telegram_id: int | None



async def list_serviceable_branch_ids(
    session_factory: SessionFactory,
    now: datetime | None = None,
) -> list[UUID]:
    """Branch ids that should be polled: active branches of paying / trialing users."""
    current = ensure_aware(now) or utcnow()
    stmt = (
        select(CompanyBranch.id)
        .join(User, User.id == CompanyBranch.user_id)
        .join(Subscription, Subscription.user_id == User.id)
        .where(CompanyBranch.is_active.is_(True), BillingService.serviceable_filter(current))
    )
    async with session_factory() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def ingest_scraped_reviews(
    session: AsyncSession,
    branch_id: UUID,
    scraped: list[ScrapedReview],
) -> IngestStats:
    """Persist freshly scraped reviews, skipping ones already stored (dedup by external id)."""
    if not scraped:
        return IngestStats()

    external_ids = [review.external_id for review in scraped]
    existing_result = await session.execute(
        select(Review.external_id).where(
            Review.branch_id == branch_id,
            Review.external_id.in_(external_ids),
        )
    )
    known = set(existing_result.scalars().all())

    created = 0
    for scraped_review in scraped:
        if scraped_review.external_id in known:
            continue
        session.add(
            Review(
                branch_id=branch_id,
                external_id=scraped_review.external_id,
                author_name=scraped_review.author_name,
                rating=scraped_review.rating,
                text=scraped_review.text,
                status=ReviewStatus.NEW,
            )
        )
        known.add(scraped_review.external_id)
        created += 1

    if created:
        await session.commit()

    stats = IngestStats(created=created, duplicates=len(scraped) - created)
    logger.info(
        "Ingested reviews for branch %s: %s new, %s duplicates",
        branch_id,
        stats.created,
        stats.duplicates,
    )
    return stats


async def scrape_branch(
    session_factory: SessionFactory,
    branch_id: UUID,
    scraper_factory: ScraperFactory,
    now: datetime | None = None,
) -> ScrapeOutcome:
    """Poll one branch: fetch reviews from the platform and store the unknown ones."""
    async with session_factory() as session:
        result = await session.execute(
            select(CompanyBranch).where(CompanyBranch.id == branch_id)
        )
        branch = result.scalar_one_or_none()
        if branch is None:
            logger.warning("Branch %s disappeared before scraping", branch_id)
            return ScrapeOutcome(status="not_found", branch_id=branch_id, reason="branch not found")
        if not branch.is_active:
            logger.info("Branch %s is suspended (billing), scraping skipped", branch_id)
            return ScrapeOutcome(status="skipped", branch_id=branch_id, reason="branch is inactive")

        platform_type = branch.platform_type
        platform_url = branch.platform_url

    scraper = scraper_factory(platform_type)
    if scraper is None:
        return ScrapeOutcome(
            status="skipped",
            branch_id=branch_id,
            reason=f"platform {platform_type.value} is not supported yet",
        )

    scraped = await scraper.fetch_reviews(platform_url)
    async with session_factory() as session:
        stats = await ingest_scraped_reviews(session, branch_id, scraped)

    return ScrapeOutcome(
        status="success",
        branch_id=branch_id,
        created=stats.created,
        duplicates=stats.duplicates,
    )


async def list_new_review_ids(
    session_factory: SessionFactory,
    limit: int = 10,
    now: datetime | None = None,
) -> list[UUID]:
    """Reviews that still need an AI draft (status NEW) for serviceable branches."""
    current = ensure_aware(now) or utcnow()
    stmt = (
        select(Review.id)
        .join(CompanyBranch, Review.branch_id == CompanyBranch.id)
        .join(User, User.id == CompanyBranch.user_id)
        .join(Subscription, Subscription.user_id == User.id)
        .where(Review.status == ReviewStatus.NEW, BillingService.serviceable_filter(current))
        .order_by(Review.created_at)
        .limit(limit)
    )
    async with session_factory() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def generate_reply_for_review(
    session_factory: SessionFactory,
    review_id: UUID,
    llm_client: LLMClient | None = None,
    review_text: str | None = None,
    tone_of_voice: str | ToneOfVoice | None = None,
) -> GeneratedReply | None:
    """Draft an AI reply, persist it and move the review to PENDING_APPROVAL."""
    async with session_factory() as session:
        result = await session.execute(
            select(Review)
            .options(selectinload(Review.branch).selectinload(CompanyBranch.user))
            .where(Review.id == review_id)
        )
        review = result.scalar_one_or_none()
        if review is None:
            logger.warning("Review %s not found for AI generation", review_id)
            return None
        if review.branch is None:
            logger.warning("Review %s has no branch attached", review_id)
            return None

        branch = review.branch
        text = review_text or review.text
        tone = ToneOfVoice(tone_of_voice) if tone_of_voice else branch.tone_of_voice
        client = llm_client or LLMClient()

        reply = await client.generate_reply(text, tone)

        review.generated_reply = reply
        review.status = ReviewStatus.PENDING_APPROVAL
        await session.commit()
        await session.refresh(review)

        owner: User | None = branch.user
        logger.info("Generated reply for review %s (branch %s)", review.id, branch.id)

        return GeneratedReply(
            review_id=review.id,
            generated_reply=reply,
            review_text=text,
            rating=review.rating,
            author_name=review.author_name,
            branch_name=branch.name,
            platform_name=get_platform_display_name(branch.platform_type),
            telegram_id=owner.telegram_id if owner else None,
        )

