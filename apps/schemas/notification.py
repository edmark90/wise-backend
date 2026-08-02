from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class NotificationCreate(BaseModel):
    title: str
    message: Optional[str] = None
    notification_type: str = "General Announcement"
    category: Optional[str] = None
    priority: str = "Normal"
    target: str = "All"
    target_value: Optional[str] = None
    schedule_id: Optional[int] = None
    collection_date: Optional[str] = None
    reason: Optional[str] = None
    reason_other: Optional[str] = None
    additional_message: Optional[str] = None
    send_now: bool = True
    recipients: Optional[list[str]] = None
    created_by: Optional[int] = None

class NotificationUpdate(BaseModel):
    title: Optional[str] = None
    message: Optional[str] = None
    target: Optional[str] = None
    target_value: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None

class DeviceTokenCreate(BaseModel):
    token: str
    platform: str = "android"


class NotificationListItem(BaseModel):
    """Notification schema for admin history lists.

    Includes the full field set so the click-to-detail panel renders accurate
    data without a second fetch.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    notification_type: Optional[str] = None
    title: str
    message: str
    category: Optional[str] = None
    priority: Optional[str] = None
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    affected_barangays: Optional[str] = None
    reason: Optional[str] = None
    reason_other: Optional[str] = None
    additional_message: Optional[str] = None
    collection_date: Optional[str] = None
    collection_time: Optional[str] = None
    assigned_personnel: Optional[str] = None
    recipients: Optional[str] = None
    status: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: datetime


class NotificationResponse(BaseModel):
    """Full notification schema for detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message: str
    notification_type: Optional[str] = None
    category: Optional[str] = None
    target: Optional[str] = None
    target_value: Optional[str] = None
    schedule_id: Optional[int] = None
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    affected_barangays: Optional[str] = None
    collection_date: Optional[str] = None
    collection_time: Optional[str] = None
    assigned_personnel: Optional[str] = None
    reason: Optional[str] = None
    reason_other: Optional[str] = None
    additional_message: Optional[str] = None
    priority: Optional[str] = None
    recipients: Optional[str] = None
    status: Optional[str] = None
    created_by: Optional[int] = None
    created_by_name: Optional[str] = None
    created_at: datetime
    is_read: bool = False


class MyNotificationListItem(BaseModel):
    """Notification schema returned to mobile citizens."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message: str
    notification_type: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    route_name: Optional[str] = None
    affected_barangays: Optional[str] = None
    reason: Optional[str] = None
    additional_message: Optional[str] = None
    collection_date: Optional[str] = None
    collection_time: Optional[str] = None
    created_at: datetime
    is_read: bool = False


class NotificationListResponse(BaseModel):
    notifications: list[NotificationListItem]
    total: int
    page: int
    page_size: int


class MyNotificationListResponse(BaseModel):
    notifications: list[MyNotificationListItem]
    total: int
    unread: int


class NotificationSummary(BaseModel):
    today: int
    delayed_routes: int
    cancelled_routes: int
    manual_announcements: int


class NotificationSettingsUpdate(BaseModel):
    notif_collection_updates: Optional[bool] = None
    notif_route_updates: Optional[bool] = None
    notif_announcements: Optional[bool] = None
    notif_emergency_alerts: Optional[bool] = None
    notif_reminders: Optional[bool] = None
    notif_completed_collection: Optional[bool] = None
    preferred_barangays: Optional[list[str]] = None
