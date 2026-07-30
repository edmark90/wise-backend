from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class NotificationCreate(BaseModel):
    title: str
    message: str
    target: str = "All"
    created_by: Optional[int] = None

class NotificationUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    target: Optional[str] = None


class NotificationListItem(BaseModel):
    """Lightweight notification schema for list views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    message: str
    created_at: datetime


class NotificationResponse(BaseModel):
    """Full notification schema for detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message: str
    target: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime

class NotificationListResponse(BaseModel):
    notifications: list[NotificationListItem]
    total: int
    page: int
    page_size: int
