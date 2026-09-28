from uuid import UUID
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.db.models.branch import CompanyBranch
from reput_ai.db.session import get_async_db
from reput_ai.bot.bot import create_bot
from reput_ai.bot.service import TelegramBotService

router = APIRouter(prefix="/funnel", tags=["funnel"])
logger = logging.getLogger("reput_ai.api.funnel")


class FeedbackSubmission(BaseModel):
    branch_id: UUID
    rating: int = Field(..., ge=1, le=5)
    author_name: str
    phone_or_contact: str | None = None
    feedback_text: str


@router.get("/qr/{branch_id}")
async def handle_qr_redirect(
    branch_id: UUID,
    rating: int | None = None,
    db: AsyncSession = Depends(get_async_db),
) -> RedirectResponse:
    """Feedback Funnel:

    4-5 star reviews are routed to the public maps platform URL.
    1-3 star reviews stay inside the internal direct feedback form.
    """
    stmt = select(CompanyBranch).where(CompanyBranch.id == branch_id)
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    if rating and rating >= 4:
        return RedirectResponse(url=branch.platform_url)
    return RedirectResponse(url=f"/feedback/form?branch_id={branch_id}")


@router.post("/submit")
async def submit_direct_feedback(
    submission: FeedbackSubmission,
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    stmt = (
        select(CompanyBranch)
        .options(selectinload(CompanyBranch.user))
        .where(CompanyBranch.id == submission.branch_id)
    )
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    # If rating is negative (1-3 stars), dispatch direct alert to Telegram
    if submission.rating <= 3 and branch.user and branch.user.telegram_id:
        try:
            bot = create_bot()
            bot_service = TelegramBotService(bot)
            await bot_service.send_negative_feedback_alert(
                telegram_id=branch.user.telegram_id,
                branch_name=branch.name,
                rating=submission.rating,
                author_name=submission.author_name,
                phone_or_contact=submission.phone_or_contact,
                feedback_text=submission.feedback_text,
            )
            await bot.session.close()
        except Exception as e:
            logger.error(f"Failed to dispatch negative feedback alert to Telegram: {e}")

    return {
        "status": "success",
        "message": "Thank you! Your feedback has been sent directly to management.",
    }

