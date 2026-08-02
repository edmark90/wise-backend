from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
import json
from apps.database import get_db
from apps.schemas.notification import (
    NotificationCreate,
    NotificationUpdate,
    NotificationResponse,
    NotificationListResponse,
    MyNotificationListResponse,
    NotificationSummary,
    DeviceTokenCreate,
    NotificationSettingsUpdate,
)
from apps.services.notification import (
    get_notifications,
    get_notification_summary,
    get_notification_by_id,
    create_notification,
    create_manual_announcement,
    update_notification,
    delete_notification,
    get_my_notifications,
    get_unread_count,
    mark_notification_read,
    register_device_token,
    MANUAL_TYPES,
)
from apps.utils.jwt import get_current_admin, get_current_user
from apps.models.user import User

router = APIRouter()


@router.get("/", response_model=NotificationListResponse)
def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    notification_type: Optional[str] = None,
    barangay: Optional[str] = None,
    route: Optional[str] = None,
    collection_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Admin notification history with search and filters."""
    skip = (page - 1) * page_size
    result = get_notifications(
        db,
        skip=skip,
        limit=page_size,
        search=search,
        notification_type=notification_type,
        barangay=barangay,
        route=route,
        collection_date=collection_date,
    )
    return {
        "notifications": result["notifications"],
        "total": result["total"],
        "page": page,
        "page_size": page_size,
    }


@router.get("/summary", response_model=NotificationSummary)
def notification_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Counts for the Notification Center summary cards."""
    return get_notification_summary(db)


@router.get("/my", response_model=MyNotificationListResponse)
def my_notifications(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Notifications relevant to the signed-in citizen (mobile)."""
    notifications, read_ids, unread = get_my_notifications(db, current_user, limit)
    items = []
    for n in notifications:
        item = n.__dict__.copy()
        item["is_read"] = n.id in read_ids
        items.append(item)
    return {
        "notifications": items,
        "total": len(items),
        "unread": unread,
    }


@router.post("/my/{notification_id}/read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    mark_notification_read(db, current_user, notification_id)
    return {"ok": True}


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return {"unread": get_unread_count(db, current_user)}


@router.post("/device-token")
def register_token(
    payload: DeviceTokenCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    register_device_token(db, current_user, payload.token.strip(), payload.platform)
    return {"ok": True}


@router.get("/settings")
def get_notification_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """The citizen's notification preferences + preferred barangays."""
    return {
        "notif_collection_updates": bool(current_user.notif_collection_updates),
        "notif_route_updates": bool(current_user.notif_route_updates),
        "notif_announcements": bool(current_user.notif_announcements),
        "notif_emergency_alerts": bool(current_user.notif_emergency_alerts),
        "notif_reminders": bool(current_user.notif_reminders),
        "notif_completed_collection": bool(getattr(current_user, "notif_completed_collection", True)),
        "preferred_barangays": _preferred_barangays(current_user),
    }


@router.put("/settings")
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save the citizen's notification preferences + preferred barangays."""
    data = payload.model_dump(exclude_unset=True)
    if "preferred_barangays" in data:
        current_user.preferred_barangays = json.dumps(data.pop("preferred_barangays"), ensure_ascii=False)
    for key, value in data.items():
        setattr(current_user, key, value)
    db.commit()
    db.refresh(current_user)
    return {
        "notif_collection_updates": bool(current_user.notif_collection_updates),
        "notif_route_updates": bool(current_user.notif_route_updates),
        "notif_announcements": bool(current_user.notif_announcements),
        "notif_emergency_alerts": bool(current_user.notif_emergency_alerts),
        "notif_reminders": bool(current_user.notif_reminders),
        "notif_completed_collection": bool(getattr(current_user, "notif_completed_collection", True)),
        "preferred_barangays": _preferred_barangays(current_user),
    }


def _preferred_barangays(user) -> list:
    """Decode the user's preferred barangays JSON (falls back to legacy barangay)."""
    raw = getattr(user, "preferred_barangays", None)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                names = [str(x).strip() for x in data if str(x).strip()]
                if names:
                    return names
        except Exception:
            pass
    return [user.barangay] if getattr(user, "barangay", None) else []


@router.get("/{notification_id}", response_model=NotificationResponse)
def get_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get a specific notification by ID."""
    notification = get_notification_by_id(db, notification_id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    return notification


@router.post("/", response_model=NotificationResponse, status_code=status.HTTP_201_CREATED)
def create_new_notification(
    notification_data: NotificationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create a new notification.

    Only the three manual types are supported in the Notification Module:
    General Announcement, Delayed Collection, Cancelled Collection.
    Recipients are resolved automatically — the admin never picks them.
    """
    if notification_data.notification_type in MANUAL_TYPES:
        return create_manual_announcement(db, notification_data.model_dump(), current_user)
    data = notification_data.model_dump()
    data.pop("recipients", None)
    data.pop("send_now", None)
    data["created_by"] = current_user.id
    data["created_by_name"] = current_user.fullname
    data.setdefault("status", "Sent" if data.get("send_now", True) else "Draft")
    return create_notification(db, data)


@router.put("/{notification_id}", response_model=NotificationResponse)
def update_existing_notification(
    notification_id: int,
    notification_data: NotificationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Update an existing notification."""
    notification = update_notification(db, notification_id, notification_data.model_dump(exclude_unset=True))
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    return notification


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Delete a notification."""
    notification = delete_notification(db, notification_id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    return None
