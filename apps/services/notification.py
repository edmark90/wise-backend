import json
from datetime import date, datetime, time
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, load_only

from apps.models.collection_schedule import CollectionSchedule
from apps.models.device_token import DeviceToken
from apps.models.notification import Notification
from apps.models.notification_read import NotificationRead
from apps.models.user import User
from apps.services.push import send_push_notifications

# Manual notification types (Notification Module) — the admin never selects
# recipients; recipients are resolved automatically from user preferences.
MANUAL_TYPES = [
    "General Announcement",
    "Delayed Collection",
    "Cancelled Collection",
]

# Automatic notification types — generated from the Collection Schedule module.
AUTOMATIC_TYPES = [
    "Upcoming Collection",
    "Garbage Truck Arriving",
    "Garbage Truck Arrived",
    "Collection Completed",
    "Collection Rescheduled",
]

NOTIFICATION_TYPES = MANUAL_TYPES + AUTOMATIC_TYPES

# Reason options shown in the admin Delayed/Cancelled modal
REASON_OPTIONS = [
    "Heavy Rain", "Flood", "Truck Breakdown", "Mechanical Failure",
    "Heavy Traffic", "Road Closure", "Holiday", "Emergency", "Others",
]

STATUS_TO_TYPE = {
    "Upcoming": "Upcoming Collection",
    "Arriving": "Garbage Truck Arriving",
    "Arrived": "Garbage Truck Arrived",
    "Completed": "Collection Completed",
    "Delayed": "Delayed Collection",
    "Cancelled": "Cancelled Collection",
}

# Notification type -> preference toggle that gates delivery to a citizen.
PREFERENCE_FOR_TYPE = {
    "Upcoming Collection": "notif_collection_updates",
    "Garbage Truck Arriving": "notif_route_updates",
    "Garbage Truck Arrived": "notif_route_updates",
    "Delayed Collection": "notif_route_updates",
    "Cancelled Collection": "notif_route_updates",
    "Collection Completed": "notif_completed_collection",
    "Collection Rescheduled": "notif_collection_updates",
    "General Announcement": "notif_announcements",
}

def _route_name(schedule: CollectionSchedule) -> str:
    if schedule.route_name:
        return schedule.route_name
    if schedule.starting_point:
        return schedule.starting_point
    return f"{schedule.barangay} Route"

def _time_str(schedule: CollectionSchedule) -> str:
    if not schedule.collection_time:
        return ""
    return schedule.collection_time.strftime("%I:%M %p")

def _affected_barangays(schedule: CollectionSchedule) -> List[str]:
    return [b for b in [schedule.barangay, schedule.zone] if b]

def _default_message(notification_type: str, barangay: str, route: str,
                     time_str: str, reason: Optional[str],
                     additional_message: Optional[str] = None,
                     reason_other: Optional[str] = None) -> str:
    if reason == "Others" and reason_other:
        reason = reason_other
    if notification_type == "Upcoming Collection":
        return (f"Upcoming garbage collection for {barangay} at {time_str}. "
                f"Please have your waste ready.")
    if notification_type == "Garbage Truck Arriving":
        return (f"The garbage truck is arriving in {barangay} today at {time_str}. "
                f"Please have your waste ready.")
    if notification_type == "Garbage Truck Arrived":
        return (f"The garbage truck has arrived in {barangay} today at {time_str}. "
                f"Please bring out your waste now.")
    if notification_type == "Collection Completed":
        return (f"Garbage collection for {barangay} has been completed. "
                f"Thank you for your cooperation.")
    if notification_type == "Collection Rescheduled":
        return (f"Garbage collection for {barangay} has been rescheduled to a new date. "
                f"Please check your updated collection schedule.")
    if notification_type in ("Delayed Collection", "Collection Delayed"):
        msg = ("Today's garbage collection for the following barangays has been delayed.\n"
               f"Affected Route: {route}\nAffected Barangays: {barangay}")
        if reason:
            msg += f"\nReason: {reason}"
        if additional_message:
            msg += f"\n\n{additional_message}"
        return msg
    if notification_type in ("Cancelled Collection", "Collection Cancelled"):
        msg = ("Today's garbage collection has been cancelled.\n"
               f"Affected Route: {route}\nAffected Barangays: {barangay}")
        if reason:
            msg += f"\nReason: {reason}"
        if additional_message:
            msg += f"\n\n{additional_message}"
        return msg
    return ""

def generate_route_notification(
    db: Session,
    schedule: CollectionSchedule,
    notification_type: str,
    reason: Optional[str] = None,
    reason_other: Optional[str] = None,
    additional_message: Optional[str] = None,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
    all_schedules: Optional[List[CollectionSchedule]] = None,
):
    """Create a notification for a route status change and push it.

    Deduplicated: only one "Sent" notification per (schedule, type, day) is
    stored so auto-status write-backs never spam citizens.

    When [all_schedules] is given (batch creation), a single "Upcoming
    Collection" notification is built that lists EVERY route/barangay for the
    day, and the affected barangays are the union across all of them.
    """
    today = date.today()
    existing = db.query(Notification).filter(
        Notification.schedule_id == schedule.id,
        Notification.notification_type == notification_type,
        Notification.status == "Sent",
        Notification.created_at >= datetime.combine(today, time.min),
        Notification.created_at <= datetime.combine(today, time.max),
    ).first()
    if existing:
        return existing

    all_schedules = [s for s in (all_schedules or []) if s is not None] or [schedule]
    barangay = schedule.barangay
    route = _route_name(schedule)
    time_str = _time_str(schedule)
    affected = _affected_barangays(schedule)
    title = notification_type

    if len(all_schedules) > 1:
        affected = list(dict.fromkeys(
            b for s in all_schedules for b in _affected_barangays(s)
        ))
        route = ", ".join(dict.fromkeys(_route_name(s) for s in all_schedules))
        message = _batch_upcoming_message(all_schedules, reason, additional_message)
    else:
        message = _default_message(notification_type, barangay, route, time_str, reason, additional_message, reason_other)
    if not message:
        message = schedule.remarks or notification_type

    notification = Notification(
        title=title,
        message=message,
        notification_type=notification_type,
        target=", ".join(affected) if affected else barangay,
        schedule_id=schedule.id,
        route_name=route,
        starting_point=schedule.starting_point,
        affected_barangays=json.dumps(affected, ensure_ascii=False),
        collection_date=str(schedule.collection_date),
        collection_time=time_str,
        assigned_personnel=schedule.assigned_personnel,
        reason=reason,
        reason_other=reason_other,
        additional_message=additional_message,
        priority="Important" if notification_type in ("Cancelled Collection", "Delayed Collection") else "Normal",
        recipients=json.dumps(affected, ensure_ascii=False),
        status="Sent",
        created_by=created_by,
        created_by_name=created_by_name,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    _push_to_barangays(db, affected, title, message, notification)
    return notification


def _batch_upcoming_message(schedules: List[CollectionSchedule], reason: Optional[str],
                            additional_message: Optional[str] = None) -> str:
    """Build the summary message for a multi-route "Upcoming Collection" day."""
    first = schedules[0]
    date_label = first.collection_date.strftime("%B %d, %Y") if first.collection_date else ""
    lines = [f"Upcoming garbage collection on {date_label}:"]
    for s in schedules:
        time_str = _time_str(s)
        areas = ", ".join(_affected_barangays(s)) or _route_name(s)
        lines.append(f"\u2022 {areas}" + (f" — {time_str}" if time_str else ""))
    lines.append("Please have your waste ready.")
    msg = "\n".join(lines)
    if reason:
        msg += f"\nReason: {reason}"
    if additional_message:
        msg += f"\n\n{additional_message}"
    return msg

def handle_status_change(
    db: Session,
    schedule: CollectionSchedule,
    new_status: str,
    reason: Optional[str] = None,
    reason_other: Optional[str] = None,
    additional_message: Optional[str] = None,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
):
    """Generate the matching notification when a route's status changes."""
    notification_type = STATUS_TO_TYPE.get(new_status)
    if not notification_type:
        return None
    return generate_route_notification(
        db, schedule, notification_type, reason, reason_other,
        additional_message, created_by, created_by_name,
    )

def generate_reschedule_notification(
    db: Session,
    schedule: CollectionSchedule,
    old_date,
    old_status: str,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
):
    """Notify citizens when a Cancelled/Delayed route is moved to a new date.

    Called from the schedule update flow (single edit or "Move All Routes").
    Deduplicated: only one "Sent" reschedule notification per (schedule, date).
    """
    new_date = schedule.collection_date
    if not new_date or new_date == old_date:
        return None
    notification_type = "Collection Rescheduled"

    existing = db.query(Notification).filter(
        Notification.schedule_id == schedule.id,
        Notification.notification_type == notification_type,
        Notification.collection_date == str(new_date),
    ).first()
    if existing:
        return existing

    affected = _affected_barangays(schedule)
    route = _route_name(schedule)
    time_str = _time_str(schedule)
    new_label = new_date.strftime("%B %d, %Y")
    if old_status == "Cancelled":
        first_line = f"The previously cancelled garbage collection for {', '.join(affected)} has been rescheduled."
    else:
        first_line = f"The delayed garbage collection for {', '.join(affected)} has been rescheduled."
    message = (
        first_line + "\n"
        f"New Date: {new_label}"
        + (f"\nCollection Time: {time_str}" if time_str else "")
        + "\nPlease have your waste ready on the new schedule."
    )

    notification = Notification(
        title=notification_type,
        message=message,
        notification_type=notification_type,
        target=", ".join(affected) if affected else schedule.barangay,
        schedule_id=schedule.id,
        route_name=route,
        starting_point=schedule.starting_point,
        affected_barangays=json.dumps(affected, ensure_ascii=False),
        collection_date=str(new_date),
        collection_time=time_str,
        assigned_personnel=schedule.assigned_personnel,
        priority="Normal",
        recipients=json.dumps(affected, ensure_ascii=False),
        status="Sent",
        created_by=created_by,
        created_by_name=created_by_name,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    _push_to_barangays(db, affected, notification_type, message, notification)
    return notification

def create_manual_announcement(db: Session, data: dict, current_user: User):
    """Create a manual notification (one of the three manual types).

    Recipients are ALWAYS resolved automatically:
    - General Announcement -> all active citizens with announcements ON.
    - Delayed/Cancelled Collection -> citizens whose preferred barangays
      intersect the affected route's barangays and who have route updates ON.

    The admin never picks recipients, barangays, or users.
    """
    ntype = data.get("notification_type", "General Announcement")
    title = data.get("title") or ntype
    send_now = data.get("send_now", True)
    priority = data.get("priority", "Normal")
    category = data.get("category")

    if ntype in ("Delayed Collection", "Cancelled Collection") and priority not in ("Important", "Emergency"):
        priority = "Important"

    notification = Notification(
        title=title,
        notification_type=ntype,
        category=category,
        priority=priority,
        status="Sent" if send_now else "Draft",
        created_by=current_user.id,
        created_by_name=current_user.fullname,
    )

    if ntype == "General Announcement":
        message = data.get("message") or ""
        if not message:
            raise ValueError("Announcement message is required.")
        notification.message = message
        notification.target = "All"
        notification.recipients = "All"
    else:
        # Delayed Collection / Cancelled Collection — resolved from a route.
        schedule = None
        schedule_id = data.get("schedule_id")
        if schedule_id:
            schedule = db.query(CollectionSchedule).filter(
                CollectionSchedule.id == int(schedule_id)
            ).first()
        if not schedule:
            raise ValueError("A route must be selected for this notification type.")
        route = _route_name(schedule)
        affected = _affected_barangays(schedule)
        time_str = _time_str(schedule)
        additional = data.get("additional_message") or ""
        reason = data.get("reason") or ""
        reason_other = data.get("reason_other") or ""
        message = _default_message(ntype, schedule.barangay, route, time_str, reason, additional, reason_other)
        notification.message = message
        notification.schedule_id = schedule.id
        notification.route_name = route
        notification.starting_point = schedule.starting_point
        notification.affected_barangays = json.dumps(affected, ensure_ascii=False)
        notification.collection_date = str(schedule.collection_date)
        notification.collection_time = time_str
        notification.assigned_personnel = schedule.assigned_personnel
        notification.reason = reason or None
        notification.reason_other = reason_other or None
        notification.additional_message = additional or None
        notification.target = ", ".join(affected)
        notification.recipients = json.dumps(affected, ensure_ascii=False)

    db.add(notification)
    db.commit()
    db.refresh(notification)

    if notification.status == "Sent":
        if ntype == "General Announcement":
            _push_general(db, notification)
        else:
            _push_to_barangays(
                db,
                json.loads(notification.affected_barangays or "[]"),
                notification.title,
                notification.message,
                notification,
            )
    return notification

# ---------------------------------------------------------------------------
# Recipient resolution (preference-aware, preferred-barangays matching)
# ---------------------------------------------------------------------------

def _user_preferred_barangays(user: User) -> List[str]:
    """A citizen's preferred barangays from mobile settings.

    Returns an empty list when nothing is saved yet. Callers must treat an
    empty list as "all barangays" so a fresh install receives every barangay
    by default (matching the mobile's all-selected default).
    """
    raw = user.preferred_barangays
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                names = [str(x).strip() for x in data if str(x).strip()]
                if names:
                    return names
        except Exception:
            pass
    return []

def _matching_citizens(db: Session, barangays: List[str], pref: str) -> List[int]:
    """Citizens whose preferred barangays intersect the affected barangays,
    honoring the given preference toggle (OFF citizens are skipped).

    Citizens with no saved preference (empty list) are treated as subscribed
    to every barangay — they match any affected barangay."""
    barangays = [b for b in barangays if b]
    if not barangays:
        return []
    users = db.query(User).filter(
        User.role == "citizen",
        User.is_active.is_(True),
        getattr(User, pref).is_(True),
    ).all()
    matched = []
    for u in users:
        prefs = _user_preferred_barangays(u)
        if not prefs or any(b in prefs for b in barangays):
            matched.append(u.id)
    return matched

def _push_general(db: Session, notification: Notification) -> int:
    """Broadcast a General Announcement to every active citizen with the
    announcements toggle ON."""
    user_ids = [
        u[0] for u in db.query(User.id).filter(
            User.role == "citizen",
            User.is_active.is_(True),
            getattr(User, "notif_announcements").is_(True),
        ).all()
    ]
    if not user_ids:
        return 0
    tokens = [t[0] for t in db.query(DeviceToken.token)
              .filter(DeviceToken.user_id.in_(user_ids)).all()]
    return send_push_notifications(
        tokens, notification.title, notification.message,
        {"id": str(notification.id), "type": notification.notification_type or "General Announcement"},
    )

def _push_to_barangays(db: Session, affected: List[str], title: str,
                       message: str, notification: Notification) -> int:
    ntype = notification.notification_type or ""
    pref = PREFERENCE_FOR_TYPE.get(ntype, "notif_route_updates")
    user_ids = _matching_citizens(db, affected, pref)
    if not user_ids:
        return 0
    tokens = [t[0] for t in db.query(DeviceToken.token)
              .filter(DeviceToken.user_id.in_(user_ids)).all()]
    return send_push_notifications(
        tokens, title, message,
        {"id": str(notification.id), "type": ntype},
    )

# ---------------------------------------------------------------------------
# Queries (admin)
# ---------------------------------------------------------------------------

def get_notifications(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    notification_type: Optional[str] = None,
    barangay: Optional[str] = None,
    route: Optional[str] = None,
    collection_date: Optional[str] = None,
):
    """Admin notification history with search + filters."""
    query = db.query(Notification)

    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            Notification.title.ilike(like),
            Notification.route_name.ilike(like),
            Notification.reason.ilike(like),
            Notification.affected_barangays.ilike(like),
            Notification.notification_type.ilike(like),
        ))
    if notification_type and notification_type != "All":
        if notification_type == "Automatic":
            query = query.filter(Notification.notification_type.in_(AUTOMATIC_TYPES))
        elif notification_type == "Manual":
            query = query.filter(Notification.notification_type.in_(MANUAL_TYPES))
        else:
            query = query.filter(Notification.notification_type == notification_type)
    if barangay and barangay != "All":
        query = query.filter(Notification.affected_barangays.like(f"%{barangay}%"))
    if route and route != "All":
        query = query.filter(Notification.route_name.ilike(f"%{route}%"))
    if collection_date:
        query = query.filter(Notification.collection_date == collection_date)

    total = query.count()
    notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
    return {"notifications": notifications, "total": total}


def get_notification_summary(db: Session) -> dict:
    """Counts for the Notification Center summary cards."""
    today = date.today()
    start = datetime.combine(today, time.min)
    end = datetime.combine(today, time.max)
    today_count = db.query(Notification).filter(
        Notification.created_at >= start, Notification.created_at <= end,
    ).count()
    delayed = db.query(CollectionSchedule).filter(
        CollectionSchedule.status == "Delayed",
    ).count()
    cancelled = db.query(CollectionSchedule).filter(
        CollectionSchedule.status == "Cancelled",
    ).count()
    manual = db.query(Notification).filter(
        Notification.notification_type.in_(MANUAL_TYPES),
    ).count()
    return {
        "today": today_count,
        "delayed_routes": delayed,
        "cancelled_routes": cancelled,
        "manual_announcements": manual,
    }


def get_notification_by_id(db: Session, notification_id: int):
    return db.query(Notification).filter(Notification.id == notification_id).first()


def create_notification(db: Session, notification_data: dict):
    db_notification = Notification(**notification_data)
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification


def update_notification(db: Session, notification_id: int, notification_data: dict):
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
    db_notification = get_notification_by_id(db, notification_id)
    if not db_notification:
        return None
    db.delete(db_notification)
    db.commit()
    return db_notification

# ---------------------------------------------------------------------------
# Mobile (citizen) queries
# ---------------------------------------------------------------------------

def _user_scope(db: Session, user: User, query):
    """Notifications a citizen should see: targeted to any of their preferred
    barangays, or broadcast to everyone ('All').

    A citizen with no saved preference (empty list) is subscribed to every
    barangay, so they see every notification."""
    prefs = _user_preferred_barangays(user)
    if not prefs:
        return query
    clauses = [Notification.target == "All"]
    for b in prefs:
        clauses.append(Notification.affected_barangays.like(f'%"{b}"%'))
        clauses.append(Notification.affected_barangays.like(f"%{b}%"))
    return query.filter(or_(*clauses))


def get_my_notifications(db: Session, user: User, limit: int = 50):
    """Notifications relevant to a citizen + read state."""
    query = _user_scope(db, user, db.query(Notification))
    notifications = query.order_by(Notification.created_at.desc()).limit(limit).all()
    read_ids = set(
        r[0] for r in db.query(NotificationRead.notification_id)
        .filter(NotificationRead.user_id == user.id).all()
    )
    unread = sum(1 for n in notifications if n.id not in read_ids)
    return notifications, read_ids, unread


def get_unread_count(db: Session, user: User) -> int:
    notification_ids = [
        r[0] for r in _user_scope(db, user, db.query(Notification.id)).all()
    ]
    if not notification_ids:
        return 0
    read_ids = set(
        r[0] for r in db.query(NotificationRead.notification_id)
        .filter(
            NotificationRead.user_id == user.id,
            NotificationRead.notification_id.in_(notification_ids),
        ).all()
    )
    return len(notification_ids) - len(read_ids)


def mark_notification_read(db: Session, user: User, notification_id: int):
    existing = db.query(NotificationRead).filter(
        NotificationRead.notification_id == notification_id,
        NotificationRead.user_id == user.id,
    ).first()
    if not existing:
        db.add(NotificationRead(notification_id=notification_id, user_id=user.id))
        db.commit()
    return True


def register_device_token(db: Session, user: User, token: str, platform: str = "android"):
    existing = db.query(DeviceToken).filter(DeviceToken.token == token).first()
    if existing:
        existing.user_id = user.id
        existing.platform = platform
    else:
        db.add(DeviceToken(user_id=user.id, token=token, platform=platform))
    db.commit()
    return True
