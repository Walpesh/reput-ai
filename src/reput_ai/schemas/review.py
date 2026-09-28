from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from reput_ai.db.models.review import ReviewStatus


class ReviewBase(BaseModel):
    branch_id: UUID
    external_id: str
    author_name: str
    rating: int = Field(..., ge=1, le=5)
    text: str


class ReviewCreate(ReviewBase):
    pass


class ReviewStatusUpdate(BaseModel):
    status: ReviewStatus
    final_reply: str | None = None


class ReviewRead(ReviewBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    generated_reply: str | None = None
    final_reply: str | None = None
    status: ReviewStatus
    created_at: datetime
