from sqlalchemy.orm import Session, load_only
from typing import Optional
from apps.models.notification import Notification


def get_notifications(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    created_by: Optional[int] = None,
    target: Optional[str] = None
):
    """Get notifications with filtering and pagination.
    Only selects essential columns (id, message, created_at) for list performance.
    """
    query = db.query(Notification).options(
        load_only(Notification.id, Notification.message, Notification.created_at)
    )
    
    if created_by:
        query = query.filter(Notification.created_by == created_by)
    
    if target:
        query = query.filter(Notification.target == target)
    
    total = query.count()
    notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"notifications": notifications, "total": total}


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
