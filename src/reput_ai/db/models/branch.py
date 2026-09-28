import enum
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from reput_ai.db.base import Base

if TYPE_CHECKING:
    from reput_ai.db.models.user import User
    from reput_ai.db.models.review import Review


class PlatformType(str, enum.Enum):
    YANDEX = "YANDEX"
    GIS2 = "GIS2"
    GOOGLE = "GOOGLE"
    AVITO = "AVITO"


class ToneOfVoice(str, enum.Enum):
    OFFICIAL = "OFFICIAL"
    FRIENDLY = "FRIENDLY"
    HUMOROUS = "HUMOROUS"


class CompanyBranch(Base):
    __tablename__ = "company_branches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform_type: Mapped[PlatformType] = mapped_column(
        Enum(PlatformType, name="platform_type_enum"),
        nullable=False,
    )
    platform_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    tone_of_voice: Mapped[ToneOfVoice] = mapped_column(
        Enum(ToneOfVoice, name="tone_of_voice_enum"),
        default=ToneOfVoice.OFFICIAL,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="branches")
    reviews: Mapped[list["Review"]] = relationship(
        "Review",
        back_populates="branch",
        cascade="all, delete-orphan",
    )
