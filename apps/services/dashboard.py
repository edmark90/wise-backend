from sqlalchemy.orm import Session
from sqlalchemy import func

from datetime import datetime, date
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_history import CollectionHistory
from apps.models.notification import Notification

def get_dashboard_stats(db: Session):
    """Get dashboard statistics.
    All counts use COUNT() aggregate queries — never retrieves full records.
    Matches actual DB schema (classified_at, confidence, disposal_category).
    """
    today = date.today()
    
    # Total users
    total_users = db.query(func.count(User.id)).scalar() or 0
    
    # Total waste records
    total_waste_records = db.query(func.count(WasteRecord.id)).scalar() or 0
    
    # Total AI classifications (waste records with confidence score)
    total_ai_classifications = db.query(func.count(WasteRecord.id)).filter(
        WasteRecord.confidence.isnot(None)
    ).scalar() or 0
    
    # Today's classifications — using classified_at column
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())
    todays_classifications = db.query(func.count(WasteRecord.id)).filter(
        WasteRecord.classified_at >= today_start,
        WasteRecord.classified_at <= today_end
    ).scalar() or 0
    
    # Today's collection schedule
    todays_collection_schedule = db.query(func.count(CollectionSchedule.id)).filter(
        CollectionSchedule.collection_date == today
    ).scalar() or 0
    
    # Upcoming collections
    upcoming_collections = db.query(func.count(CollectionSchedule.id)).filter(
        CollectionSchedule.status == "Upcoming"
    ).scalar() or 0
    
    # Completed collections (from history)
    completed_collections = db.query(func.count(CollectionHistory.id)).scalar() or 0
    
    # Total notifications (no is_read column in actual DB)
    total_notifications = db.query(func.count(Notification.id)).scalar() or 0
    
    return {
        "total_users": total_users,
        "total_waste_records": total_waste_records,
        "total_ai_classifications": total_ai_classifications,
        "todays_classifications": todays_classifications,
        "todays_collection_schedule": todays_collection_schedule,
        "upcoming_collections": upcoming_collections,
        "completed_collections": completed_collections,
        "total_notifications": total_notifications
    }
