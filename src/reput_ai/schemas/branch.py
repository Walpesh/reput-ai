from uuid import UUID
from pydantic import BaseModel, ConfigDict, HttpUrl
from reput_ai.db.models.branch import PlatformType, ToneOfVoice


class BranchBase(BaseModel):
    name: str
    platform_type: PlatformType
    platform_url: str
    tone_of_voice: ToneOfVoice = ToneOfVoice.OFFICIAL
    is_active: bool = True


class BranchCreate(BranchBase):
    pass


class BranchUpdate(BaseModel):
    name: str | None = None
    tone_of_voice: ToneOfVoice | None = None
    is_active: bool | None = None


class BranchRead(BranchBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
