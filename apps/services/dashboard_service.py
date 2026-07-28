from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, date
from apps.model.user import User
from apps.model.waste_record import WasteRecord
from apps.model.collection_schedule import CollectionSchedule
from apps.model.collection_history import CollectionHistory
from apps.model.notification import Notification

def get_dashboard_stats(db: Session):
    """Get dashboard statistics."""
    today = date.today()
    
    # Total users
    total_users = db.query(User).count()
    
    # Total waste records
    total_waste_records = db.query(WasteRecord).count()
    
    # Total AI classifications (waste records with confidence score)
    total_ai_classifications = db.query(WasteRecord).filter(
        WasteRecord.confidence_score.isnot(None)
    ).count()
    
    # Today's classifications
    todays_classifications = db.query(WasteRecord).filter(
        func.date(WasteRecord.created_at) == today
    ).count()
    
    # Today's collection schedule
    todays_collection_schedule = db.query(CollectionSchedule).filter(
        func.date(CollectionSchedule.collection_date) == today
    ).count()
    
    # Pending collections
    pending_collections = db.query(CollectionSchedule).filter(
        CollectionSchedule.status == "pending"
    ).count()
    
    # Completed collections (from history)
    completed_collections = db.query(CollectionHistory).count()
    
    # Unread notifications
    unread_notifications = db.query(Notification).filter(
        Notification.is_read == False
    ).count()
    
    return {
        "total_users": total_users,
        "total_waste_records": total_waste_records,
        "total_ai_classifications": total_ai_classifications,
        "todays_classifications": todays_classifications,
        "todays_collection_schedule": todays_collection_schedule,
        "pending_collections": pending_collections,
        "completed_collections": completed_collections,
        "unread_notifications": unread_notifications
    }
