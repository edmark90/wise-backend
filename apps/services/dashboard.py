from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError

from datetime import datetime, date, time, timedelta
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_history import CollectionHistory
from apps.models.notification import Notification
from apps.utils.ph_time import ph_today

def get_dashboard_stats(db: Session):
    """Get dashboard statistics.
    All counts use COUNT() aggregate queries — never retrieves full records.
    Matches actual DB schema (classified_at, confidence, disposal_category).
    """
    today = ph_today()
    
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
    
    # Completed collections (from history). The collection_history table may
    # not exist yet on a stale deployment — fall back to 0 instead of 500.
    try:
        completed_collections = db.query(func.count(CollectionHistory.id)).scalar() or 0
    except ProgrammingError:
        completed_collections = 0
    
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


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """First and last instant of a month (PH)."""
    first_day = date(year, month, 1)
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    return (
        datetime.combine(first_day, time.min),
        datetime.combine(next_first, time.min) - timedelta(seconds=1),
    )


def get_monthly_dashboard_stats(db: Session, year: int, month: int):
    """Month-scoped stats feeding the dashboard line + pie charts.

    `records_by_day` -> [{day, count}] for the trend line.
    `per_class`      -> {waste_type: count} for the pie/donut.
    Notifications and collection routes are also scoped to the month.
    """
    start, end = _month_bounds(year, month)
    today = ph_today()

    # Waste records + per-class counts for the selected month
    records_query = db.query(func.count(WasteRecord.id)).filter(
        WasteRecord.classified_at >= start,
        WasteRecord.classified_at <= end
    )
    total_waste_records = records_query.scalar() or 0

    class_rows = db.query(
        WasteRecord.waste_type,
        func.count(WasteRecord.id).label("cnt"),
    ).filter(
        WasteRecord.classified_at >= start,
        WasteRecord.classified_at <= end,
    ).group_by(WasteRecord.waste_type).all()

    per_class = {row.waste_type: int(row.cnt) for row in class_rows}

    # Daily counts -> line chart
    day_rows = db.query(
        func.dayofmonth(WasteRecord.classified_at).label("day"),
        func.count(WasteRecord.id).label("cnt"),
    ).filter(
        WasteRecord.classified_at >= start,
        WasteRecord.classified_at <= end,
    ).group_by(func.dayofmonth(WasteRecord.classified_at)).all()

    records_by_day = {int(r.day): int(r.cnt) for r in day_rows}

    # Daily counts per waste class -> multi-line chart
    class_day_rows = db.query(
        func.dayofmonth(WasteRecord.classified_at).label("day"),
        WasteRecord.waste_type,
        func.count(WasteRecord.id).label("cnt"),
    ).filter(
        WasteRecord.classified_at >= start,
        WasteRecord.classified_at <= end,
    ).group_by(
        func.dayofmonth(WasteRecord.classified_at),
        WasteRecord.waste_type,
    ).all()

    records_by_class_by_day: dict[str, dict[int, int]] = {}
    for r in class_day_rows:
        records_by_class_by_day.setdefault(r.waste_type, {})[int(r.day)] = int(r.cnt)

    # Avg AI confidence for the month
    avg_confidence = db.query(func.avg(WasteRecord.confidence)).filter(
        WasteRecord.confidence.isnot(None),
        WasteRecord.classified_at >= start,
        WasteRecord.classified_at <= end,
    ).scalar()

    # Collection routes scheduled within the month
    routes_query = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date >= start.date(),
        CollectionSchedule.collection_date <= end.date(),
    )
    total_routes = routes_query.count()

    # Notifications created within the month
    total_notifications = db.query(func.count(Notification.id)).filter(
        Notification.created_at >= start,
        Notification.created_at <= end,
    ).scalar() or 0

    upcoming_routes = routes_query.filter(
        CollectionSchedule.collection_date > today
    ).count()

    return {
        "year": year,
        "month": month,
        "total_waste_records": total_waste_records,
        "total_ai_classifications": total_waste_records,
        "per_class": per_class,
        "records_by_day": records_by_day,
        "records_by_class_by_day": records_by_class_by_day,
        "avg_confidence": round(float(avg_confidence), 1) if avg_confidence is not None else None,
        "total_routes": total_routes,
        "upcoming_routes": upcoming_routes,
        "total_notifications": total_notifications,
        "total_users": db.query(func.count(User.id)).scalar() or 0,
    }
