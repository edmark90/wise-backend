from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError

from datetime import datetime, date, time, timedelta
from apps.models.user import User
from apps.models.waste_record import WasteRecord
from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_history import CollectionHistory
from apps.models.notification import Notification
from apps.utils.ph_time import ph_today, ph_now

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


def get_dashboard_activity(db: Session):
    """Recent activity feed for the dashboard.
    Merges recent waste classifications, collection completions, user
    registrations, and notifications into a unified, time-sorted list.
    """
    now = ph_now()
    cutoff = now - timedelta(days=7)
    activities = []

    # Recent waste classifications
    try:
        recent_records = db.query(WasteRecord).filter(
            WasteRecord.classified_at >= cutoff
        ).order_by(WasteRecord.classified_at.desc()).limit(20).all()
        for r in recent_records:
            activities.append({
                "id": f"wr_{r.id}",
                "type": "waste_classified",
                "message": f"Waste classified as {r.waste_type} ({r.confidence}% confidence)" if r.confidence else f"Waste classified as {r.waste_type}",
                "created_at": r.classified_at.isoformat() if r.classified_at else now.isoformat(),
                "barangay": None,
                "metadata": {"waste_type": r.waste_type, "disposal_category": r.disposal_category},
            })
    except ProgrammingError:
        pass

    # Recent collection completions
    try:
        recent_completions = db.query(CollectionHistory).filter(
            CollectionHistory.created_at >= cutoff
        ).order_by(CollectionHistory.created_at.desc()).limit(20).all()
        for c in recent_completions:
            activities.append({
                "id": f"ch_{c.id}",
                "type": "collection_complete",
                "message": f"Collection completed at {c.area}" + (f" — {c.waste_collected_kg} kg" if c.waste_collected_kg else ""),
                "created_at": c.created_at.isoformat() if c.created_at else now.isoformat(),
                "barangay": None,
                "metadata": {"area": c.area, "waste_collected_kg": c.waste_collected_kg},
            })
    except ProgrammingError:
        pass

    # Recent user registrations
    try:
        recent_users = db.query(User).filter(
            User.created_at >= cutoff
        ).order_by(User.created_at.desc()).limit(10).all()
        for u in recent_users:
            activities.append({
                "id": f"usr_{u.id}",
                "type": "user_created",
                "message": f"New user registered: {u.fullname}",
                "created_at": u.created_at.isoformat() if u.created_at else now.isoformat(),
                "barangay": getattr(u, "barangay", None),
                "metadata": {},
            })
    except (ProgrammingError, AttributeError):
        pass

    # Recent notifications sent
    try:
        recent_notifs = db.query(Notification).filter(
            Notification.created_at >= cutoff
        ).order_by(Notification.created_at.desc()).limit(10).all()
        for n in recent_notifs:
            activities.append({
                "id": f"ntf_{n.id}",
                "type": "notification_sent",
                "message": n.title if hasattr(n, "title") and n.title else "Notification sent",
                "created_at": n.created_at.isoformat() if n.created_at else now.isoformat(),
                "barangay": None,
                "metadata": {},
            })
    except (ProgrammingError, AttributeError):
        pass

    # Sort by created_at descending and cap at 50
    activities.sort(key=lambda a: a["created_at"], reverse=True)
    return {"activities": activities[:50]}


def get_dashboard_collection_rail(db: Session):
    """7-day collection operations rail for the dashboard.
    Returns a day-by-day summary for the past 3 days through 3 days ahead,
    showing scheduled vs completed collections per day.
    """
    today = ph_today()
    start_date = today - timedelta(days=3)
    end_date = today + timedelta(days=3)

    # Fetch all schedules in the window
    schedules = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date >= start_date,
        CollectionSchedule.collection_date <= end_date,
    ).all()

    # Group by date
    by_date: dict[date, list] = {}
    for s in schedules:
        d = s.collection_date
        by_date.setdefault(d, []).append(s)

    # Fetch completions in the window
    try:
        completions = db.query(CollectionHistory).filter(
            CollectionHistory.collection_date >= datetime.combine(start_date, time.min),
            CollectionHistory.collection_date <= datetime.combine(end_date, time.max),
        ).all()
        completed_by_date: dict[date, int] = {}
        for c in completions:
            d = c.collection_date.date() if isinstance(c.collection_date, datetime) else c.collection_date
            completed_by_date[d] = completed_by_date.get(d, 0) + 1
    except ProgrammingError:
        completed_by_date = {}

    days = []
    current = start_date
    while current <= end_date:
        day_schedules = by_date.get(current, [])
        total_count = len(day_schedules)
        completed_count = completed_by_date.get(current, 0)

        if total_count == 0:
            status = "pending"
        elif completed_count >= total_count:
            status = "completed"
        elif completed_count > 0:
            status = "in_progress"
        else:
            status = "pending"

        days.append({
            "date": current.isoformat(),
            "status": status,
            "completed_count": completed_count,
            "total_count": total_count,
            "is_today": current == today,
            "is_past": current < today,
        })
        current += timedelta(days=1)

    return {"days": days}
