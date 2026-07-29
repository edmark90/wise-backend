from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from apps.database import get_db
from apps.schemas.notification import (
    NotificationCreate,
    NotificationUpdate,
    NotificationResponse,
    NotificationListResponse
)
from apps.services.notification import (
    get_notifications,
    get_notification_by_id,
    create_notification,
    update_notification,
    delete_notification,
    mark_as_read,
    mark_all_as_read
)
from apps.utils.jwt import get_current_admin
from apps.models.user import User

router = APIRouter()

@router.get("/", response_model=NotificationListResponse)
def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user_id: Optional[int] = None,
    is_read: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all notifications with filtering and pagination."""
    skip = (page - 1) * page_size
    result = get_notifications(
        db,
        skip=skip,
        limit=page_size,
        user_id=user_id,
        is_read=is_read
    )
    
    return {
        "notifications": result["notifications"],
        "total": result["total"],
        "unread_count": result["unread_count"],
        "page": page,
        "page_size": page_size
    }

@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get total unread notifications count."""
    from apps.models.notification import Notification
    unread_count = db.query(func.count(Notification.id)).filter(
        Notification.is_read == False
    ).scalar()
    return {"unread_count": unread_count}

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
    """Create a new notification."""
    notification = create_notification(db, notification_data.model_dump())
    return notification

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

@router.patch("/{notification_id}/mark-read", response_model=NotificationResponse)
def mark_notification_as_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Mark a notification as read."""
    notification = mark_as_read(db, notification_id)
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    return notification

@router.patch("/mark-all-read")
def mark_all_notifications_as_read(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Mark all notifications for a user as read."""
    result = mark_all_as_read(db, user_id)
    return {"success": result}

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
