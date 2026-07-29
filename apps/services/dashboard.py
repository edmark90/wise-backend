from sqlalchemy.orm import Session

from datetime import datetime, date
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_history import CollectionHistory
from apps.models.notification import Notification

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
    
    # Today's classifications (use range query for index performance)
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())
    todays_classifications = db.query(WasteRecord).filter(
        WasteRecord.created_at >= today_start,
        WasteRecord.created_at <= today_end
    ).count()
    
    # Today's collection schedule (use range query for index performance)
    todays_collection_schedule = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date >= today_start,
        CollectionSchedule.collection_date <= today_end
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
