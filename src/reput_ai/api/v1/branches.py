from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from reput_ai.api.deps import get_current_user
from reput_ai.db.models.branch import CompanyBranch
from reput_ai.db.models.user import User
from reput_ai.db.session import get_async_db
from reput_ai.schemas.branch import BranchCreate, BranchRead, BranchUpdate

router = APIRouter(prefix="/branches", tags=["branches"])


@router.get("/", response_model=list[BranchRead])
async def list_branches(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> list[CompanyBranch]:
    stmt = select(CompanyBranch).where(CompanyBranch.user_id == current_user.id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/", response_model=BranchRead, status_code=status.HTTP_201_CREATED)
async def create_branch(
    branch_in: BranchCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> CompanyBranch:
    branch = CompanyBranch(
        user_id=current_user.id,
        name=branch_in.name,
        platform_type=branch_in.platform_type,
        platform_url=str(branch_in.platform_url),
        tone_of_voice=branch_in.tone_of_voice,
        is_active=branch_in.is_active,
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch


@router.patch("/{branch_id}", response_model=BranchRead)
async def update_branch(
    branch_id: UUID,
    branch_update: BranchUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> CompanyBranch:
    stmt = select(CompanyBranch).where(
        CompanyBranch.id == branch_id,
        CompanyBranch.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    branch = result.scalar_one_or_none()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")

    update_data = branch_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(branch, field, value)

    await db.commit()
    await db.refresh(branch)
    return branch
