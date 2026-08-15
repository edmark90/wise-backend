from sqlalchemy.orm import Session, load_only
from sqlalchemy import or_
from typing import Optional, List
from datetime import date, datetime, time, timedelta
from apps.models.collection_schedule import CollectionSchedule
from apps.models.notification import Notification
from apps.services.notification import (
    STATUS_TO_TYPE,
    handle_status_change,
    generate_route_notification,
    generate_reschedule_notification,
)
from apps.utils.ph_time import ph_now, ph_today

# Automatic statuses are derived from the server clock. Manual statuses
# (Delayed/Cancelled) are set via the status-change modal and preserved.
MANUAL_STATUSES = ("Cancelled", "Delayed")
AUTO_STATUSES = ("Upcoming", "Arriving", "Arrived", "Completed")
# A route flips to "Arriving" this many minutes before its scheduled time.
ARRIVING_LEAD_MINUTES = 30
# A route flips from "Arrived" to "Completed" this many minutes after its
# scheduled time, so today's route can reach "Completed" on collection day.
COMPLETED_AFTER_MINUTES = 60


def derive_schedule_status(schedule, now: Optional[datetime] = None) -> str:
    """Compute the server-time-derived status for a single route stop.

    Manual statuses (Cancelled/Delayed) are preserved untouched. Otherwise:
      - past date              -> Completed
      - future date            -> Upcoming
      - today, > lead          -> Upcoming
      - today, in lead         -> Arriving
      - today, at/until +60min -> Arrived
      - today, after +60min    -> Completed
    """
    if schedule.status in MANUAL_STATUSES:
        return schedule.status
    d = schedule.collection_date
    t = schedule.collection_time
    if not d or not t:
        return schedule.status or "Upcoming"
    now = now or ph_now()
    if d < now.date():
        return "Completed"
    if d > now.date():
        return "Upcoming"
    sched_dt = datetime.combine(d, t)
    if now < sched_dt - timedelta(minutes=ARRIVING_LEAD_MINUTES):
        return "Upcoming"
    if now < sched_dt:
        return "Arriving"
    if now < sched_dt + timedelta(minutes=COMPLETED_AFTER_MINUTES):
        return "Arrived"
    return "Completed"


def apply_derived_statuses(schedules, now: Optional[datetime] = None):
    """Overwrite each schedule's status with its server-time-derived value.

    In-memory only (read paths) — the changes are never committed, so the
    stored column keeps manual values while responses always reflect the
    server clock.
    """
    if not schedules:
        return schedules
    now = now or ph_now()
    for s in schedules:
        s.status = derive_schedule_status(s, now)
    return schedules


def sync_auto_status_notifications(db: Session) -> int:
    """Materialize Arriving / Arrived / Completed notifications for today.

    These statuses are server-time-derived (see derive_schedule_status); this
    worker turns each threshold crossing into a real notification record (and
    FCM push to the affected barangays) the first time a route enters that
    state each day. Dedup happens inside generate_route_notification, so
    repeated scans are harmless.
    """
    emitted = 0
    today = ph_today()
    start = datetime.combine(today, time.min)
    end = datetime.combine(today, time.max)
    schedules = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date == today
    ).all()
    for s in schedules:
        if s.status in MANUAL_STATUSES:
            continue
        derived = derive_schedule_status(s)
        if derived not in ("Arriving", "Arrived", "Completed"):
            continue
        notification_type = STATUS_TO_TYPE.get(derived)
        if not notification_type:
            continue
        existing = db.query(Notification.id).filter(
            Notification.schedule_id == s.id,
            Notification.notification_type == notification_type,
            Notification.status == "Sent",
            Notification.created_at >= start,
            Notification.created_at <= end,
        ).first()
        if existing:
            continue
        if generate_route_notification(db, s, notification_type) is not None:
            emitted += 1
    return emitted


def _normalize_status(schedule_data: dict) -> dict:
    """Ensure a create/update payload never stores an automatic status.

    The four auto statuses are server-time-derived, so new routes are always
    persisted as "Upcoming" and derived on read.
    """
    status = schedule_data.get("status")
    if status in AUTO_STATUSES:
        schedule_data = dict(schedule_data)
        schedule_data["status"] = "Upcoming"
    return schedule_data

def get_collection_schedules(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    status: Optional[str] = None,
    barangay: Optional[str] = None
):
    """Get collection schedules with filtering, search, and pagination.
    Only selects essential columns for list performance.
    """
    query = db.query(CollectionSchedule).options(
        load_only(
            CollectionSchedule.id,
            CollectionSchedule.barangay,
            CollectionSchedule.zone,
            CollectionSchedule.collection_date,
            CollectionSchedule.collection_time,
            CollectionSchedule.assigned_personnel,
            CollectionSchedule.status
        )
    )
    
    if search:
        query = query.filter(
            or_(
                CollectionSchedule.barangay.ilike(f"%{search}%"),
                CollectionSchedule.zone.ilike(f"%{search}%"),
                CollectionSchedule.assigned_personnel.ilike(f"%{search}%"),
                CollectionSchedule.route_name.ilike(f"%{search}%"),
            )
        )
    
    if status:
        query = query.filter(CollectionSchedule.status == status)
    
    if barangay:
        query = query.filter(CollectionSchedule.barangay == barangay)
    
    total = query.count()
    schedules = query.order_by(
        CollectionSchedule.collection_date.asc(),
        CollectionSchedule.collection_time.asc()
    ).offset(skip).limit(limit).all()
    
    return {"schedules": apply_derived_statuses(schedules), "total": total}

def get_schedules_by_date(db: Session, target_date: date, status: Optional[str] = None):
    """Get all schedules for a specific date (calendar view).

    Optionally filter by status (e.g. "Delayed" / "Cancelled") so the
    Notification Module can auto-display affected routes for a chosen day.
    """
    query = db.query(CollectionSchedule).options(
        load_only(
            CollectionSchedule.id,
            CollectionSchedule.barangay,
            CollectionSchedule.zone,
            CollectionSchedule.route_name,
            CollectionSchedule.starting_point,
            CollectionSchedule.collection_date,
            CollectionSchedule.collection_time,
            CollectionSchedule.assigned_personnel,
            CollectionSchedule.status
        )
    ).filter(
        CollectionSchedule.collection_date == target_date
    )
    if status:
        query = query.filter(CollectionSchedule.status == status)
    return apply_derived_statuses(query.order_by(CollectionSchedule.collection_time.asc()).all())

def get_schedules_by_date_range(db: Session, start_date: date, end_date: date):
    """Get schedules within a date range (calendar / mobile view)."""
    return apply_derived_statuses(db.query(CollectionSchedule).options(
        load_only(
            CollectionSchedule.id,
            CollectionSchedule.barangay,
            CollectionSchedule.zone,
            CollectionSchedule.collection_date,
            CollectionSchedule.collection_time,
            CollectionSchedule.assigned_personnel,
            CollectionSchedule.status
        )
    ).filter(
        CollectionSchedule.collection_date >= start_date,
        CollectionSchedule.collection_date <= end_date
    ).order_by(CollectionSchedule.collection_date.asc(), CollectionSchedule.collection_time.asc()).all())

def get_collection_schedule_by_id(db: Session, schedule_id: int):
    """Get a collection schedule by ID."""
    return db.query(CollectionSchedule).filter(CollectionSchedule.id == schedule_id).first()
def create_collection_schedule(db: Session, schedule_data: dict, notify: bool = True):
    """Create a new collection schedule.

    When `notify` is True (single route creation), the backend automatically
    generates an "Upcoming Collection" notification for the newly created
    route. Batch creation (`create_collection_schedules_batch`) generates a
    single summary notification instead, so multi-route saves are not spammy.
    """
    if not schedule_data.get("route_name") and schedule_data.get("starting_point"):
        schedule_data["route_name"] = schedule_data["starting_point"]
    if not schedule_data.get("starting_point") and schedule_data.get("route_name"):
        schedule_data["starting_point"] = schedule_data["route_name"]
    schedule_data = _normalize_status(schedule_data)
    db_schedule = CollectionSchedule(**schedule_data)
    db.add(db_schedule)
    db.commit()
    db.refresh(db_schedule)

    if notify and db_schedule.status == "Upcoming":
        generate_route_notification(
            db, db_schedule, "Upcoming Collection",
        )
    return db_schedule

def create_collection_schedules_batch(
    db: Session,
    schedules: List[dict],
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
):
    """Create multiple collection schedules for the same day in one call.

    Saves all routes, then the backend automatically generates a single
    "Upcoming Collection" notification summarizing the day (e.g. "August 6
    has 5 scheduled collection routes."). Recipients are resolved from each
    citizen's preferred barangays.
    """
    created = []
    for schedule_data in schedules:
        if not schedule_data.get("route_name") and schedule_data.get("starting_point"):
            schedule_data["route_name"] = schedule_data["starting_point"]
        if not schedule_data.get("starting_point") and schedule_data.get("route_name"):
            schedule_data["starting_point"] = schedule_data["route_name"]
        schedule_data = _normalize_status(schedule_data)
        db_schedule = CollectionSchedule(**schedule_data)
        db.add(db_schedule)
        db.commit()
        db.refresh(db_schedule)
        created.append(db_schedule)

    if created:
        first = created[0]
        if first.status == "Upcoming":
            generate_route_notification(
                db, first, "Upcoming Collection",
                created_by=created_by,
                created_by_name=created_by_name,
                all_schedules=created,
            )
    return created

def update_collection_schedule(db: Session, schedule_id: int, schedule_data: dict,
                               created_by: Optional[int] = None,
                               created_by_name: Optional[str] = None):
    """Update a collection schedule. Fires a notification when the status changes."""
    db_schedule = get_collection_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None

    old_status = db_schedule.status
    old_date = db_schedule.collection_date
    new_status = schedule_data.get("status")
    reason = schedule_data.pop("reason", None)
    reason_other = schedule_data.pop("reason_other", None)
    additional_message = schedule_data.pop("additional_message", None)

    # The four automatic statuses are server-time-derived and never stored;
    # ignore any payload that tries to set them so reads always reflect the
    # server clock.
    if new_status in AUTO_STATUSES:
        schedule_data.pop("status", None)
        new_status = None

    for key, value in schedule_data.items():
        if value is not None:
            setattr(db_schedule, key, value)

    db.commit()
    db.refresh(db_schedule)

    # Moving a Cancelled/Delayed route to a new date resets it to Upcoming
    # (fresh lifecycle) while still notifying citizens that the previously
    # Cancelled/Delayed collection is now scheduled for a new date.
    if old_status in ("Cancelled", "Delayed") and db_schedule.collection_date != old_date:
        if db_schedule.status != "Upcoming":
            db_schedule.status = "Upcoming"
            db.commit()
            db.refresh(db_schedule)
        generate_reschedule_notification(
            db, db_schedule, old_date, old_status,
            created_by=created_by, created_by_name=created_by_name,
        )
    elif new_status and new_status != old_status:
        handle_status_change(
            db, db_schedule, new_status, reason, reason_other,
            additional_message, created_by, created_by_name,
        )
    return db_schedule

def set_schedule_status(db: Session, schedule_id: int, new_status: str,
                        reason: Optional[str] = None,
                        reason_other: Optional[str] = None,
                        additional_message: Optional[str] = None,
                        created_by: Optional[int] = None,
                        created_by_name: Optional[str] = None):
    """Set a route's status manually (Delayed/Cancelled flow) + notify.

    The four automatic statuses (Upcoming/Arriving/Arrived/Completed) are
    server-time-derived and cannot be set manually.
    """
    if new_status not in MANUAL_STATUSES:
        raise ValueError("Only Delayed or Cancelled can be set manually.")
    db_schedule = get_collection_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None

    old_status = db_schedule.status
    db_schedule.status = new_status
    db.commit()
    db.refresh(db_schedule)

    if new_status != old_status:
        handle_status_change(
            db, db_schedule, new_status, reason, reason_other,
            additional_message, created_by, created_by_name,
        )
    return db_schedule

def delete_collection_schedule(db: Session, schedule_id: int):
    """Delete a collection schedule."""
    db_schedule = get_collection_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None
    
    db.delete(db_schedule)
    db.commit()
    return db_schedule
