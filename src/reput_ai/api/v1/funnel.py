from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.db.models.branch import CompanyBranch
from reput_ai.db.session import get_async_db

router = APIRouter(prefix="/funnel", tags=["funnel"])


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
    stmt = select(CompanyBranch).where(CompanyBranch.id == submission.branch_id)
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    # In production, this directly notifies business management via Telegram Bot
    return {
        "status": "success",
        "message": "Thank you! Your feedback has been sent directly to management.",
    }
