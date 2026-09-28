"""AI processing tasks: draft replies for new reviews and notify the owner."""

import logging
from uuid import UUID

from reput_ai.bot.bot import create_bot
from reput_ai.bot.service import TelegramBotService
from reput_ai.config import settings
from reput_ai.db.session import async_session_maker
from reput_ai.llm.client import LLMClient
from reput_ai.services.review_pipeline import generate_reply_for_review, list_new_review_ids
from reput_ai.workers.celery_app import celery_app
from reput_ai.workers.runtime import run_async

logger = logging.getLogger("reput_ai.workers.ai_tasks")


def build_llm_client() -> LLMClient:
    """LLM client factory (tests patch it to stub the OpenRouter HTTP layer)."""
    return LLMClient()


async def _draft_and_notify(
    review_id: UUID,
    review_text: str | None,
    tone_of_voice: str | None,
) -> dict[str, object]:
    generated = await generate_reply_for_review(
        async_session_maker,
        review_id,
        llm_client=build_llm_client(),
        review_text=review_text,
        tone_of_voice=tone_of_voice,
    )
    if generated is None:
        return {"status": "not_found", "review_id": str(review_id)}
    if generated.telegram_id is None:
        logger.info("Review %s drafted, owner has no Telegram account linked", review_id)
        return {"status": "generated", "review_id": str(review_id), "notified": False}

    bot = create_bot()
    try:
        notified = await TelegramBotService(bot).notify_new_review(
            telegram_id=generated.telegram_id,
            review_id=generated.review_id,
            branch_name=generated.branch_name,
            platform_name=generated.platform_name,
            author_name=generated.author_name,
            rating=generated.rating,
            review_text=generated.review_text,
            generated_reply=generated.generated_reply,
        )
    finally:
        await bot.session.close()

    return {"status": "generated", "review_id": str(review_id), "notified": notified}


@celery_app.task(name="reput_ai.workers.tasks.ai_tasks.generate_review_reply_task")
def generate_review_reply_task(
    review_id: str,
    review_text: str | None = None,
    tone_of_voice: str | None = None,
) -> dict[str, object]:
    """Generate (or regenerate) the AI draft and send it to the owner for approval."""
    result = run_async(_draft_and_notify(UUID(review_id), review_text, tone_of_voice))
    logger.info("AI processing finished for review %s: %s", review_id, result.get("status"))
    return result


@celery_app.task(name="reput_ai.workers.tasks.ai_tasks.process_new_reviews")
def process_new_reviews() -> dict[str, object]:
    """Periodic dispatcher: queue an AI draft for every review in status NEW."""
    review_ids = run_async(list_new_review_ids(async_session_maker, limit=settings.AI_BATCH_SIZE))
    for review_id in review_ids:
        generate_review_reply_task.delay(str(review_id))

    logger.info("Queued AI generation for %s review(s)", len(review_ids))
    return {"status": "success", "queued": len(review_ids)}

