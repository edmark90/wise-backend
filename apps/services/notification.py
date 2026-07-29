from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from apps.models.notification import Notification

def get_notifications(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    user_id: Optional[int] = None,
    is_read: Optional[bool] = None
):
    """Get notifications with filtering and pagination."""
    query = db.query(Notification)
    
    if user_id:
        query = query.filter(Notification.user_id == user_id)
    
    if is_read is not None:
        query = query.filter(Notification.is_read == is_read)
    
    total = query.count()
    unread_count = db.query(func.count(Notification.id)).filter(
        Notification.is_read == False
    ).scalar() or 0
    notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"notifications": notifications, "total": total, "unread_count": unread_count}

def get_notification_by_id(db: Session, notification_id: int):
    """Get a notification by ID."""
    return db.query(Notification).filter(Notification.id == notification_id).first()

def create_notification(db: Session, notification_data: dict):
    """Create a new notification."""
    db_notification = Notification(**notification_data)
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification

def update_notification(db: Session, notification_id: int, notification_data: dict):
    """Update a notification."""
    db_notification = get_notification_by_id(db, notification_id)
    if not db_notification:
        return None
    
    for key, value in notification_data.items():
        if value is not None:
            setattr(db_notification, key, value)
    
    db.commit()
    db.refresh(db_notification)
    return db_notification

def delete_notification(db: Session, notification_id: int):
    """Delete a notification."""
    db_notification = get_notification_by_id(db, notification_id)
    if not db_notification:
        return None
    
    db.delete(db_notification)
    db.commit()
    return db_notification

def mark_as_read(db: Session, notification_id: int):
    """Mark a notification as read."""
    db_notification = get_notification_by_id(db, notification_id)
    if not db_notification:
        return None
    
    db_notification.is_read = True
    db.commit()
    db.refresh(db_notification)
    return db_notification

def mark_all_as_read(db: Session, user_id: int):
    """Mark all notifications for a user as read."""
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return True
