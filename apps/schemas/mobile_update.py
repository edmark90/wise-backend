from pydantic import BaseModel, ConfigDict, HttpUrl
from typing import Optional
from datetime import datetime

class AppVersionCreate(BaseModel):
    version: str
    version_code: int
    title: str
    release_notes: Optional[str] = None
    apk_url: str
    is_force: bool = False

class AppVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    version_code: int
    title: str
    release_notes: Optional[str] = None
    apk_url: str
    is_force: bool
    created_by: Optional[int] = None
    created_at: datetime

class SendMobileUpdateIn(BaseModel):
    title: str
    message: str
    app_version_id: int
    force: bool = False
    reminder: str = "None"

class SendUpdateOut(BaseModel):
    id: int
    title: str
    version: str
    force: bool
    reminder: str
    devices_pushed: int
    fcm_enabled: bool

class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message: str
    app_version_id: int
    force: bool
    reminder: str
    sent_reminders: int
    created_at: datetime
    app_version: Optional[AppVersionOut] = None

class LatestUpdateOut(BaseModel):
    update_available: bool
    latest: Optional[AppVersionOut] = None
    current_version_code: Optional[int] = None
