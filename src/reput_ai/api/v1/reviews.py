from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.api.deps import get_current_user
from reput_ai.db.models.branch import CompanyBranch
from reput_ai.db.models.review import Review
from reput_ai.db.models.user import User
from reput_ai.db.session import get_async_db
from reput_ai.schemas.review import ReviewCreate, ReviewRead, ReviewStatusUpdate

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/", response_model=list[ReviewRead])
async def list_reviews(
    branch_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> list[Review]:
    stmt = (
        select(Review)
        .join(CompanyBranch, Review.branch_id == CompanyBranch.id)
        .where(CompanyBranch.user_id == current_user.id)
    )
    if branch_id:
        stmt = stmt.where(Review.branch_id == branch_id)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/", response_model=ReviewRead, status_code=status.HTTP_201_CREATED)
async def create_review(
    review_in: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Review:
    branch_stmt = select(CompanyBranch).where(
        CompanyBranch.id == review_in.branch_id,
        CompanyBranch.user_id == current_user.id,
    )
    branch_res = await db.execute(branch_stmt)
    branch = branch_res.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    review = Review(
        branch_id=review_in.branch_id,
        external_id=review_in.external_id,
        author_name=review_in.author_name,
        rating=review_in.rating,
        text=review_in.text,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review


@router.patch("/{review_id}/status", response_model=ReviewRead)
async def update_review_status(
    review_id: UUID,
    status_update: ReviewStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Review:
    stmt = (
        select(Review)
        .join(CompanyBranch, Review.branch_id == CompanyBranch.id)
        .where(
            Review.id == review_id,
            CompanyBranch.user_id == current_user.id,
        )
    )
    result = await db.execute(stmt)
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    review.status = status_update.status
    if status_update.final_reply is not None:
        review.final_reply = status_update.final_reply

    await db.commit()
    await db.refresh(review)
    return review
