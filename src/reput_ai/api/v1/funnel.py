"""QR code / short-link feedback funnel (negative feedback interceptor).

Routing rules (stage 3.3):

* 4-5 stars -> the customer is forwarded to the public review boards
  (Yandex Maps / 2GIS / Google / Avito) so positive reviews go public.
* 1-3 stars -> the feedback never reaches the public boards: it is intercepted and
  delivered straight to management via Telegram.
"""

from uuid import UUID

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.bot.bot import create_bot
from reput_ai.bot.service import TelegramBotService
from reput_ai.db.models.branch import CompanyBranch
from reput_ai.db.session import get_async_db
from reput_ai.services.funnel import (
    build_short_link,
    decode_branch_code,
    encode_branch_code,
    route_feedback,
)

router = APIRouter(prefix="/funnel", tags=["funnel"])
logger = logging.getLogger("reput_ai.api.funnel")


class FeedbackSubmission(BaseModel):
    branch_id: UUID
    rating: int = Field(..., ge=1, le=5)
    author_name: str
    phone_or_contact: str | None = None
    feedback_text: str


class FunnelEntry(BaseModel):
    """Landing payload of the QR short link (headless clients / web widget)."""

    branch_id: UUID
    branch_name: str
    rating: int | None = None
    mode: str
    redirect_url: str
    public_url: str
    feedback_url: str


async def _load_branch(db: AsyncSession, branch_id: UUID) -> CompanyBranch:
    stmt = (
        select(CompanyBranch)
        .options(selectinload(CompanyBranch.user))
        .where(CompanyBranch.id == branch_id)
    )
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    return branch


async def _resolve_short_link(db: AsyncSession, code: str):
    branch_id = decode_branch_code(code)
    if branch_id is None:
        raise HTTPException(status_code=404, detail="Unknown short link")
    branch = await _load_branch(db, branch_id)
    return branch


@router.get("/short-link/{branch_id}")
async def get_branch_short_link(
    branch_id: UUID,
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    """Short link printed on the QR stand (points at the rating router)."""
    branch = await _load_branch(db, branch_id)
    return {
        "branch_id": str(branch.id),
        "code": encode_branch_code(branch.id),
        "short_url": build_short_link(branch.id),
    }


@router.get("/s/{code}")
async def follow_short_link(
    code: str,
    rating: int | None = None,
    db: AsyncSession = Depends(get_async_db),
) -> RedirectResponse:
    """Short-link login: the scanned QR points here and the rating decides the route."""
    branch = await _resolve_short_link(db, code)
    route = route_feedback(branch, rating)
    logger.info("Funnel short link %s resolved to %s (%s)", code, route.redirect_url, route.mode)
    return RedirectResponse(url=route.redirect_url, status_code=307)


@router.get("/s/{code}/entry", response_model=FunnelEntry)
async def short_link_entry(
    code: str,
    rating: int | None = None,
    db: AsyncSession = Depends(get_async_db),
) -> FunnelEntry:
    """JSON entry point of the QR funnel (used by the web widget / mobile clients)."""
    branch = await _resolve_short_link(db, code)
    route = route_feedback(branch, rating)
    return FunnelEntry(
        branch_id=branch.id,
        branch_name=branch.name,
        rating=rating,
        mode=route.mode,
        redirect_url=route.redirect_url,
        public_url=route.public_url,
        feedback_url=route.feedback_url,
    )


@router.get("/qr/{branch_id}")
async def handle_qr_redirect(
    branch_id: UUID,
    rating: int | None = None,
    db: AsyncSession = Depends(get_async_db),
) -> RedirectResponse:
    """Legacy QR link: 4-5 stars go to the public maps platform, 1-3 stars to the form."""
    branch = await _load_branch(db, branch_id)
    route = route_feedback(branch, rating)
    return RedirectResponse(url=route.redirect_url, status_code=307)


@router.post("/submit")
async def submit_direct_feedback(
    submission: FeedbackSubmission,
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    branch = await _load_branch(db, submission.branch_id)
    route = route_feedback(branch, submission.rating)

    if route.mode == "public":
        # 4-5 stars: the customer is forwarded to the public boards instead of the form.
        logger.info(
            "Positive feedback (%s/5) for branch %s routed to the public board",
            submission.rating,
            branch.id,
        )
        return {
            "status": "redirected",
            "public_url": route.public_url,
            "message": "Спасибо! Пожалуйста, поделитесь впечатлением на картах.",
        }

    # 1-3 stars: intercept the negative feedback and alert management immediately.
    if branch.user and branch.user.telegram_id:
        bot = create_bot()
        try:
            bot_service = TelegramBotService(bot)
            await bot_service.send_negative_feedback_alert(
                telegram_id=branch.user.telegram_id,
                branch_name=branch.name,
                rating=submission.rating,
                author_name=submission.author_name,
                phone_or_contact=submission.phone_or_contact,
                feedback_text=submission.feedback_text,
            )
        except Exception as e:
            logger.error(f"Failed to dispatch negative feedback alert to Telegram: {e}")
        finally:
            await bot.session.close()

    return {
        "status": "success",
        "message": "Thank you! Your feedback has been sent directly to management.",
    }


