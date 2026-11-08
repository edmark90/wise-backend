from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional, List
from datetime import date, datetime, time, timedelta
from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_stop import CollectionStop
from apps.models.collection_history import CollectionHistory
from apps.models.notification import Notification
from apps.models.user import User
from apps.services.notification import (
    STATUS_TO_TYPE,
    handle_status_change,
    generate_route_notification,
    generate_route_creation_notification,
    generate_reschedule_notification,
    generate_collector_stop_reminder,
    generate_stop_status_notification,
)
from apps.utils.ph_time import ph_now, ph_today

MANUAL_STATUSES = ("Cancelled", "Delayed")
CONFIRMED_STATUSES = ("Arriving", "Arrived", "Completed", "Delayed", "Cancelled")
AUTO_STATUSES = ("Upcoming", "Arriving", "Arrived", "Completed")
ARRIVING_LEAD_MINUTES = 30
AUTO_DELAY_AFTER_MINUTES = 60


def derive_stop_status(schedule, stop, now: Optional[datetime] = None) -> str:
    """Derive a single stop's operational status without falsely marking Arrived or Completed.

    Rules:
    - Never automatically mark a stop as Arrived or Completed based on wall-clock time.
    - If the stop is already Completed, Cancelled, Arrived, or Delayed, keep its confirmed status.
    - If the parent schedule is Cancelled, non-completed stops are Cancelled.
    - If now >= scheduled_time + 60 minutes and the stop has NOT been confirmed Arrived or Completed,
      it becomes Delayed.
    """
    current_status = (stop.status or "Upcoming").strip()
    if current_status in ("Completed", "Cancelled", "Arrived"):
        return current_status
    if getattr(schedule, "status", None) == "Cancelled":
        return "Cancelled"
    if current_status == "Delayed":
        return "Delayed"

    d = getattr(schedule, "collection_date", None)
    t = getattr(stop, "collection_time", None) or getattr(schedule, "collection_time", None)
    if not d or not t:
        return current_status or "Upcoming"

    now = now or ph_now()
    stop_dt = datetime.combine(d, t)

    # 1-hour overdue without arrival confirmation -> Delayed
    if now >= stop_dt + timedelta(minutes=AUTO_DELAY_AFTER_MINUTES) and getattr(stop, "arrived_at", None) is None:
        return "Delayed"

    if current_status == "Arriving":
        return "Arriving"

    return "Upcoming"


def derive_schedule_status(schedule, now: Optional[datetime] = None) -> str:
    """Compute the truthful operational status of a collection route from its stops.

    Never marks a route as Arrived or Completed unless confirmed by the Collector (or Admin).
    """
    now = now or ph_now()
    raw_status = (schedule.status or "Upcoming").strip()

    if raw_status in ("Cancelled", "Completed"):
        return raw_status

    stops = getattr(schedule, "stops", None) or []
    if stops:
        stop_statuses = [derive_stop_status(schedule, st, now) for st in stops]
        non_cancelled = [s for s in stop_statuses if s != "Cancelled"]
        if not non_cancelled:
            return "Cancelled"
        if all(s == "Completed" for s in non_cancelled):
            return "Completed"
        if any(s == "Arrived" for s in non_cancelled):
            return "Arrived"
        if any(s == "Arriving" for s in non_cancelled):
            return "Arriving"
        if any(s == "Delayed" for s in non_cancelled) or raw_status == "Delayed":
            return "Delayed"
        if any(s == "Completed" for s in non_cancelled):
            return "Arriving"
        return "Upcoming"

    # Fallback for legacy single-stop schedule without stops rows
    if raw_status in ("Arrived", "Arriving", "Delayed"):
        return raw_status
    d = schedule.collection_date
    t = schedule.collection_time
    if not d or not t:
        return raw_status or "Upcoming"
    sched_dt = datetime.combine(d, t)
    if now >= sched_dt + timedelta(minutes=AUTO_DELAY_AFTER_MINUTES):
        return "Delayed"
    return "Upcoming"


def apply_derived_statuses(schedules, now: Optional[datetime] = None):
    """Apply truthful stop-derived and overdue statuses in-memory."""
    if not schedules:
        return schedules
    now = now or ph_now()
    for s in schedules:
        if hasattr(s, "stops") and s.stops:
            for st in s.stops:
                eff = derive_stop_status(s, st, now)
                st.status = eff
                if eff == "Delayed" and not getattr(st, "delay_reason", None):
                    st.delay_reason = "Arrival not confirmed within 1 hour of scheduled time"
        s.status = derive_schedule_status(s, now)
    return schedules


def sync_auto_status_notifications(db: Session) -> int:
    """Background worker that handles:
    1) 30-minute advance reminder to the assigned Collector before each stop's scheduled time.
    2) 1-hour automatic Delay detection if a stop has not been confirmed Arrived or Completed.
    """
    emitted = 0
    now = ph_now()
    today = ph_today()

    schedules = (
        db.query(CollectionSchedule)
        .options(joinedload(CollectionSchedule.stops))
        .filter(
            CollectionSchedule.collection_date <= today,
            CollectionSchedule.status.notin_(["Completed", "Cancelled"]),
        )
        .all()
    )

    for s in schedules:
        if not s.stops:
            continue
        schedule_changed = False
        for st in sorted(s.stops, key=lambda x: x.sequence or 0):
            if st.status in ("Completed", "Cancelled"):
                continue
            stop_time = st.collection_time or s.collection_time
            if not s.collection_date or not stop_time:
                continue
            stop_dt = datetime.combine(s.collection_date, stop_time)

            # 1) 30-Minute Advance Reminder to Collector (for today's stops)
            if (
                s.collection_date == today
                and st.status in ("Upcoming", "Scheduled")
                and not getattr(st, "reminder_sent", 0)
                and (stop_dt - timedelta(minutes=ARRIVING_LEAD_MINUTES)) <= now < stop_dt
            ):
                st.reminder_sent = 1
                db.commit()
                if generate_collector_stop_reminder(db, s, st) is not None:
                    emitted += 1

            # 2) 1-Hour Automatic Delay Detection (if not confirmed Arrived or Completed)
            if (
                st.status in ("Upcoming", "Scheduled", "Arriving")
                and getattr(st, "arrived_at", None) is None
                and now >= stop_dt + timedelta(minutes=AUTO_DELAY_AFTER_MINUTES)
            ):
                st.status = "Delayed"
                st.delayed_at = st.delayed_at or now
                if not st.delay_reason:
                    st.delay_reason = "Automatic: Arrival not confirmed within 1 hour of scheduled time"
                schedule_changed = True
                db.commit()
                if generate_stop_status_notification(
                    db,
                    s,
                    st,
                    "Delayed",
                    reason=st.delay_reason,
                    created_by_name="System Auto-Monitor",
                ) is not None:
                    emitted += 1

        if schedule_changed:
            new_sched_status = derive_schedule_status(s, now)
            if s.status != new_sched_status:
                s.status = new_sched_status
                db.commit()

    return emitted


def _normalize_status(schedule_data: dict) -> dict:
    """Ensure a create payload starts in Upcoming unless explicitly Cancelled/Delayed."""
    status = schedule_data.get("status")
    if status not in MANUAL_STATUSES and status != "Completed":
        schedule_data = dict(schedule_data)
        schedule_data["status"] = "Upcoming"
    return schedule_data


def _resolve_collector_fields(db: Session, schedule_data: dict) -> dict:
    """Resolve and validate assigned_collector_id and assigned_personnel."""
    schedule_data = dict(schedule_data)
    collector_id = schedule_data.get("assigned_collector_id")
    personnel_name = (schedule_data.get("assigned_personnel") or "").strip()

    if collector_id:
        collector = db.query(User).filter(User.id == int(collector_id)).first()
        if not collector:
            raise ValueError("Selected collector account was not found.")
        if (collector.role or "").lower() != "collector":
            raise ValueError(f"User '{collector.fullname}' is not a Collector account.")
        if not collector.is_active:
            raise ValueError(f"Collector '{collector.fullname}' is currently inactive and cannot be assigned.")
        schedule_data["assigned_collector_id"] = collector.id
        schedule_data["assigned_personnel"] = collector.fullname
    elif personnel_name:
        collector = (
            db.query(User)
            .filter(
                User.role == "collector",
                User.fullname.ilike(personnel_name),
            )
            .first()
        )
        if collector:
            if not collector.is_active:
                raise ValueError(f"Collector '{collector.fullname}' is currently inactive and cannot be assigned.")
            schedule_data["assigned_collector_id"] = collector.id
            schedule_data["assigned_personnel"] = collector.fullname

    return schedule_data


def validate_schedule_conflict(
    db: Session,
    collection_date: date,
    collection_time: time,
    truck_number: Optional[str],
    assigned_personnel: Optional[str],
    exclude_id: Optional[int] = None,
    assigned_collector_id: Optional[int] = None,
):
    """Prevent double-booking the same truck or collector on the same date and time."""
    if not collection_date or not collection_time:
        return

    query = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date == collection_date,
        CollectionSchedule.collection_time == collection_time,
        CollectionSchedule.status != "Cancelled"
    )
    if exclude_id:
        query = query.filter(CollectionSchedule.id != exclude_id)

    existing_schedules = query.all()

    for s in existing_schedules:
        if truck_number and s.truck_number and s.truck_number.strip().lower() == truck_number.strip().lower():
            time_str = collection_time.strftime("%I:%M %p") if isinstance(collection_time, time) else str(collection_time)
            raise ValueError(
                f"Truck '{truck_number}' is already assigned to {s.route_name or s.barangay + ' Route'} "
                f"on {collection_date} at {time_str}. Please choose another truck or time."
            )
        if assigned_collector_id and getattr(s, "assigned_collector_id", None) and int(s.assigned_collector_id) == int(assigned_collector_id):
            time_str = collection_time.strftime("%I:%M %p") if isinstance(collection_time, time) else str(collection_time)
            raise ValueError(
                f"Collector '{assigned_personnel or s.assigned_personnel}' is already assigned to {s.route_name or s.barangay + ' Route'} "
                f"on {collection_date} at {time_str}. Please choose another collector or time."
            )
        if assigned_personnel and s.assigned_personnel and s.assigned_personnel.strip().lower() == assigned_personnel.strip().lower():
            time_str = collection_time.strftime("%I:%M %p") if isinstance(collection_time, time) else str(collection_time)
            raise ValueError(
                f"Collector '{assigned_personnel}' is already assigned to {s.route_name or s.barangay + ' Route'} "
                f"on {collection_date} at {time_str}. Please choose another collector or time."
            )


def _load_with_stops(query):
    """Helper to eagerly load collection stops ordered by sequence."""
    return query.options(joinedload(CollectionSchedule.stops))


def get_collection_schedules(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    status: Optional[str] = None,
    barangay: Optional[str] = None,
    truck_number: Optional[str] = None,
    personnel: Optional[str] = None,
):
    query = db.query(CollectionSchedule)
    if search:
        query = query.filter(
            or_(
                CollectionSchedule.barangay.ilike(f"%{search}%"),
                CollectionSchedule.zone.ilike(f"%{search}%"),
                CollectionSchedule.route_name.ilike(f"%{search}%"),
                CollectionSchedule.truck_number.ilike(f"%{search}%"),
                CollectionSchedule.assigned_personnel.ilike(f"%{search}%"),
            )
        )
    if status:
        query = query.filter(CollectionSchedule.status == status)
    if barangay:
        query = query.filter(CollectionSchedule.barangay == barangay)
    if truck_number:
        query = query.filter(CollectionSchedule.truck_number == truck_number)
    if personnel:
        query = query.filter(CollectionSchedule.assigned_personnel.ilike(f"%{personnel}%"))

    total = query.count()
    schedules = _load_with_stops(query).order_by(
        CollectionSchedule.collection_date.desc(),
        CollectionSchedule.collection_time.asc()
    ).offset(skip).limit(limit).all()

    return {"schedules": apply_derived_statuses(schedules), "total": total}


def get_schedules_by_date(
    db: Session,
    target_date: date,
    status: Optional[str] = None,
    truck_number: Optional[str] = None,
    personnel: Optional[str] = None,
    barangay: Optional[str] = None,
):
    query = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date == target_date
    )
    if status:
        query = query.filter(CollectionSchedule.status == status)
    if truck_number:
        query = query.filter(CollectionSchedule.truck_number == truck_number)
    if personnel:
        query = query.filter(CollectionSchedule.assigned_personnel.ilike(f"%{personnel}%"))
    if barangay:
        query = query.filter(CollectionSchedule.barangay == barangay)
    return apply_derived_statuses(
        _load_with_stops(query).order_by(CollectionSchedule.collection_time.asc()).all()
    )


def get_schedules_by_date_range(
    db: Session,
    start_date: date,
    end_date: date,
    truck_number: Optional[str] = None,
    personnel: Optional[str] = None,
    barangay: Optional[str] = None,
):
    query = db.query(CollectionSchedule).filter(
        CollectionSchedule.collection_date >= start_date,
        CollectionSchedule.collection_date <= end_date
    )
    if truck_number:
        query = query.filter(CollectionSchedule.truck_number == truck_number)
    if personnel:
        query = query.filter(CollectionSchedule.assigned_personnel.ilike(f"%{personnel}%"))
    if barangay:
        query = query.filter(CollectionSchedule.barangay == barangay)
    return apply_derived_statuses(
        _load_with_stops(query).order_by(
            CollectionSchedule.collection_date.asc(),
            CollectionSchedule.collection_time.asc()
        ).all()
    )


def get_collection_schedule_by_id(db: Session, schedule_id: int):
    return _load_with_stops(
        db.query(CollectionSchedule).filter(CollectionSchedule.id == schedule_id)
    ).first()


def _populate_schedule_defaults(schedule_data: dict) -> dict:
    """Auto-populate route_name, starting_point, truck_number, and primary time."""
    stops = schedule_data.get("stops") or []
    first_barangay = stops[0]["barangay"] if stops else (schedule_data.get("barangay") or "Collection")
    first_street = stops[0].get("street", "") if stops else (schedule_data.get("zone") or "")
    first_time = stops[0].get("collection_time") if stops else schedule_data.get("collection_time")

    schedule_data["barangay"] = first_barangay
    schedule_data["zone"] = first_street

    if not schedule_data.get("collection_time") and first_time:
        schedule_data["collection_time"] = first_time

    if not schedule_data.get("starting_point"):
        schedule_data["starting_point"] = first_street if first_street else f"{first_barangay} Center"

    if not schedule_data.get("route_name"):
        sp = schedule_data["starting_point"]
        if sp and sp.lower() != first_barangay.lower() and sp.lower() != f"{first_barangay.lower()} center":
            schedule_data["route_name"] = f"{first_barangay} - {sp} Route"
        else:
            schedule_data["route_name"] = f"{first_barangay} Route"

    if not schedule_data.get("remarks"):
        schedule_data["remarks"] = "Regular Scheduled Collection"

    if "truck_number" not in schedule_data or schedule_data["truck_number"] is None:
        schedule_data["truck_number"] = ""

    return schedule_data


def _upsert_stops(db: Session, schedule: CollectionSchedule, stops_data: list):
    """Replace all stops for a schedule with the given list and their individual collection times."""
    db.query(CollectionStop).filter(CollectionStop.schedule_id == schedule.id).delete()
    db.flush()

    for idx, stop in enumerate(stops_data, start=1):
        sequence = stop.get("sequence") if stop.get("sequence") else idx
        stop_time = stop.get("collection_time") or schedule.collection_time
        db_stop = CollectionStop(
            schedule_id=schedule.id,
            barangay=stop.get("barangay", ""),
            street=stop.get("street", ""),
            sequence=sequence,
            collection_time=stop_time,
            status="Upcoming",
        )
        db.add(db_stop)

    db.flush()


def create_collection_schedule(
    db: Session,
    schedule_data: dict,
    notify: bool = True,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
):
    """Create a new collection assignment with multiple stops, each having its own time."""
    stops_data = schedule_data.pop("stops", []) or []
    if not stops_data:
        barangay = schedule_data.get("barangay") or ""
        zone = schedule_data.get("zone") or ""
        ctime = schedule_data.get("collection_time")
        if barangay:
            stops_data = [{"barangay": barangay, "street": zone, "collection_time": ctime, "sequence": 1}]

    if not stops_data:
        raise ValueError("An assignment must have at least one collection stop.")

    schedule_data = _resolve_collector_fields(db, schedule_data)
    schedule_data = _populate_schedule_defaults({**schedule_data, "stops": stops_data})
    schedule_data.pop("stops", None)
    schedule_data = _normalize_status(schedule_data)

    validate_schedule_conflict(
        db,
        schedule_data.get("collection_date"),
        schedule_data.get("collection_time"),
        schedule_data.get("truck_number"),
        schedule_data.get("assigned_personnel"),
        assigned_collector_id=schedule_data.get("assigned_collector_id"),
    )

    db_schedule = CollectionSchedule(**schedule_data)
    db.add(db_schedule)
    db.flush()

    _upsert_stops(db, db_schedule, stops_data)
    db.commit()
    db.refresh(db_schedule)

    if notify and db_schedule.status == "Upcoming":
        generate_route_creation_notification(
            db, db_schedule,
            created_by=created_by,
            created_by_name=created_by_name,
        )

    return db_schedule


def create_collection_schedules_batch(
    db: Session,
    schedules: List[dict],
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
):
    """Create multiple collection assignments for a day in one call.
    Generates exactly ONE consolidated notification per newly created route.
    """
    created = []
    for schedule_data in schedules:
        stops_data = schedule_data.pop("stops", []) or []
        if not stops_data:
            barangay = schedule_data.get("barangay") or ""
            zone = schedule_data.get("zone") or ""
            ctime = schedule_data.get("collection_time")
            if barangay:
                stops_data = [{"barangay": barangay, "street": zone, "collection_time": ctime, "sequence": 1}]

        if not stops_data:
            raise ValueError("Each assignment must have at least one collection stop.")

        schedule_data = _resolve_collector_fields(db, schedule_data)
        schedule_data = _populate_schedule_defaults({**schedule_data, "stops": stops_data})
        schedule_data.pop("stops", None)
        schedule_data = _normalize_status(schedule_data)

        validate_schedule_conflict(
            db,
            schedule_data.get("collection_date"),
            schedule_data.get("collection_time"),
            schedule_data.get("truck_number"),
            schedule_data.get("assigned_personnel"),
            assigned_collector_id=schedule_data.get("assigned_collector_id"),
        )
        db_schedule = CollectionSchedule(**schedule_data)
        db.add(db_schedule)
        db.flush()
        _upsert_stops(db, db_schedule, stops_data)
        db.commit()
        db.refresh(db_schedule)
        created.append(db_schedule)

    # Generate one notification per route (not per stop or per barangay)
    for s in created:
        if s.status == "Upcoming":
            generate_route_creation_notification(
                db, s,
                created_by=created_by,
                created_by_name=created_by_name,
            )
    return created


def update_collection_schedule(
    db: Session,
    schedule_id: int,
    schedule_data: dict,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None
):
    db_schedule = get_collection_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None

    stops_data = schedule_data.pop("stops", None)
    if "assigned_collector_id" in schedule_data or "assigned_personnel" in schedule_data:
        schedule_data = _resolve_collector_fields(db, schedule_data)

    target_date = schedule_data.get("collection_date") or db_schedule.collection_date
    target_time = schedule_data.get("collection_time") or (stops_data[0].get("collection_time") if stops_data else db_schedule.collection_time)
    target_truck = schedule_data.get("truck_number") if "truck_number" in schedule_data else db_schedule.truck_number
    target_personnel = schedule_data.get("assigned_personnel") if "assigned_personnel" in schedule_data else db_schedule.assigned_personnel
    target_collector_id = schedule_data.get("assigned_collector_id") if "assigned_collector_id" in schedule_data else getattr(db_schedule, "assigned_collector_id", None)

    validate_schedule_conflict(
        db,
        target_date,
        target_time,
        target_truck,
        target_personnel,
        exclude_id=schedule_id,
        assigned_collector_id=target_collector_id,
    )

    old_status = db_schedule.status
    old_date = db_schedule.collection_date
    new_status = schedule_data.get("status")
    reason = schedule_data.pop("reason", None)
    reason_other = schedule_data.pop("reason_other", None)
    additional_message = schedule_data.pop("additional_message", None)

    if new_status in AUTO_STATUSES:
        schedule_data.pop("status", None)
        new_status = None

    for key, value in schedule_data.items():
        if value is not None:
            setattr(db_schedule, key, value)

    # Update stops if provided
    if stops_data is not None and len(stops_data) > 0:
        _upsert_stops(db, db_schedule, stops_data)
        first_stop = stops_data[0]
        db_schedule.barangay = first_stop.get("barangay", db_schedule.barangay)
        db_schedule.zone = first_stop.get("street", db_schedule.zone)
        if first_stop.get("collection_time"):
            db_schedule.collection_time = first_stop.get("collection_time")

    db.commit()
    db.refresh(db_schedule)

    if old_status in ("Cancelled", "Delayed") and db_schedule.collection_date != old_date:
        if db_schedule.status != "Upcoming":
            db_schedule.status = "Upcoming"
            for st in (db_schedule.stops or []):
                if st.status in ("Cancelled", "Delayed"):
                    st.status = "Upcoming"
                    st.cancelled_at = None
                    st.delayed_at = None
                    st.cancellation_reason = None
                    st.delay_reason = None
            db.commit()
            db.refresh(db_schedule)
        generate_reschedule_notification(
            db, db_schedule, old_date, old_status,
            created_by=created_by, created_by_name=created_by_name,
        )

    if new_status and new_status in MANUAL_STATUSES and new_status != old_status:
        handle_status_change(
            db, db_schedule, new_status,
            reason=reason, reason_other=reason_other,
            additional_message=additional_message,
            created_by=created_by, created_by_name=created_by_name,
        )

    return db_schedule


def delete_collection_schedule(db: Session, schedule_id: int):
    schedule = db.query(CollectionSchedule).filter(CollectionSchedule.id == schedule_id).first()
    if not schedule:
        return None
    db.delete(schedule)
    db.commit()
    return schedule


def set_schedule_status(
    db: Session,
    schedule_id: int,
    status: str,
    reason: Optional[str] = None,
    reason_other: Optional[str] = None,
    additional_message: Optional[str] = None,
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None
):
    schedule = get_collection_schedule_by_id(db, schedule_id)
    if not schedule:
        return None

    now = ph_now()
    effective_reason = reason_other if (reason == "Others" and reason_other) else reason

    if status in ("Cancelled", "Delayed") and not (effective_reason or additional_message):
        raise ValueError(f"A reason is required when marking a route as {status}.")

    schedule.status = status
    if effective_reason:
        schedule.remarks = f"{status}: {effective_reason}" + (f" — {additional_message}" if additional_message else "")

    # Cascade route-level status change to pending stops
    for st in (schedule.stops or []):
        if status == "Cancelled" and st.status != "Completed":
            st.status = "Cancelled"
            st.cancelled_at = st.cancelled_at or now
            st.cancellation_reason = effective_reason or additional_message or "Route cancelled"
            st.updated_by_id = created_by
        elif status == "Delayed" and st.status not in ("Completed", "Cancelled", "Arrived"):
            st.status = "Delayed"
            st.delayed_at = st.delayed_at or now
            st.delay_reason = effective_reason or additional_message or "Route delayed"
            st.updated_by_id = created_by
        elif status == "Completed" and st.status != "Cancelled":
            st.status = "Completed"
            st.arrived_at = st.arrived_at or now
            st.completed_at = st.completed_at or now
            st.updated_by_id = created_by
        elif status == "Upcoming" and st.status in ("Delayed", "Cancelled"):
            st.status = "Upcoming"
            st.delayed_at = None
            st.cancelled_at = None
            st.delay_reason = None
            st.cancellation_reason = None
            st.updated_by_id = created_by

    db.commit()
    db.refresh(schedule)
    handle_status_change(
        db, schedule, status,
        reason=reason, reason_other=reason_other,
        additional_message=additional_message,
        created_by=created_by, created_by_name=created_by_name
    )
    return schedule


def update_stop_status_action(
    db: Session,
    stop_id: int,
    new_status: str,
    current_user: User,
    reason: Optional[str] = None,
    reason_other: Optional[str] = None,
    additional_message: Optional[str] = None,
) -> CollectionStop:
    """Update a single stop's operational status (by assigned Collector or Admin).

    Validates collector ownership, records arrival/completion/delay/cancellation timestamps,
    logs CollectionHistory on completion, updates parent schedule status, and emits notifications.
    """
    stop = db.query(CollectionStop).filter(CollectionStop.id == stop_id).first()
    if not stop:
        raise LookupError("Collection stop not found.")

    schedule = get_collection_schedule_by_id(db, stop.schedule_id)
    if not schedule:
        raise LookupError("Parent collection schedule not found.")

    user_role = (current_user.role or "").lower()
    if user_role == "collector":
        # Verify the collector is assigned to this schedule
        assigned_id = getattr(schedule, "assigned_collector_id", None)
        assigned_name = (schedule.assigned_personnel or "").strip().lower()
        collector_name = (current_user.fullname or "").strip().lower()
        if assigned_id and int(assigned_id) != int(current_user.id):
            if assigned_name != collector_name:
                raise PermissionError("You can only update stops for routes assigned to you.")
        elif not assigned_id and assigned_name and assigned_name != collector_name:
            raise PermissionError("You can only update stops for routes assigned to you.")
    elif user_role != "admin":
        raise PermissionError("Only an assigned Collector or Administrator can update stop statuses.")

    normalized = (new_status or "").strip().capitalize()
    valid_statuses = ("Upcoming", "Arriving", "Arrived", "Completed", "Delayed", "Cancelled")
    if normalized not in valid_statuses:
        raise ValueError(f"Invalid stop status '{new_status}'. Allowed: {', '.join(valid_statuses)}")

    if stop.status == "Cancelled" and normalized == "Completed":
        raise ValueError("Cannot mark a cancelled stop as Completed.")

    effective_reason = reason_other if (reason == "Others" and reason_other) else reason
    if normalized in ("Cancelled", "Delayed") and not (effective_reason or additional_message):
        raise ValueError(f"Please provide a reason when marking a stop as {normalized}.")

    now = ph_now()
    stop.status = normalized
    stop.updated_by_id = current_user.id

    if normalized == "Arriving":
        stop.arriving_at = stop.arriving_at or now
    elif normalized == "Arrived":
        stop.arriving_at = stop.arriving_at or now
        stop.arrived_at = stop.arrived_at or now
    elif normalized == "Completed":
        stop.arriving_at = stop.arriving_at or now
        stop.arrived_at = stop.arrived_at or now
        stop.completed_at = now
        if additional_message:
            stop.remarks = additional_message
        # Record in collection_history for dashboard metrics
        try:
            area_label = f"{stop.street}, {stop.barangay}".strip(", ") if stop.street else stop.barangay
            history_entry = CollectionHistory(
                schedule_id=schedule.id,
                personnel_id=current_user.id,
                collection_date=datetime.combine(schedule.collection_date, stop.collection_time or schedule.collection_time or time(8, 0)),
                area=area_label,
                completion_date=now,
                remarks=f"Completed by {current_user.fullname}",
            )
            db.add(history_entry)
        except Exception:
            pass
    elif normalized == "Delayed":
        stop.delayed_at = now
        stop.delay_reason = effective_reason or additional_message
        if additional_message:
            stop.remarks = f"Delayed: {effective_reason} — {additional_message}" if effective_reason else f"Delayed: {additional_message}"
        elif effective_reason:
            stop.remarks = f"Delayed: {effective_reason}"
    elif normalized == "Cancelled":
        stop.cancelled_at = now
        stop.cancellation_reason = effective_reason or additional_message
        if additional_message:
            stop.remarks = f"Cancelled: {effective_reason} — {additional_message}" if effective_reason else f"Cancelled: {additional_message}"
        elif effective_reason:
            stop.remarks = f"Cancelled: {effective_reason}"
    elif normalized == "Upcoming":
        stop.arriving_at = None
        stop.arrived_at = None
        stop.completed_at = None
        stop.delayed_at = None
        stop.cancelled_at = None
        stop.delay_reason = None
        stop.cancellation_reason = None

    db.flush()

    # Re-evaluate parent schedule status from all stops
    parent_status = derive_schedule_status(schedule, now)
    schedule.status = parent_status
    db.commit()
    db.refresh(stop)
    db.refresh(schedule)

    # Emit notification for citizens / admin / collector
    if normalized in ("Arriving", "Arrived", "Completed", "Delayed", "Cancelled"):
        generate_stop_status_notification(
            db,
            schedule,
            stop,
            normalized,
            reason=reason,
            reason_other=reason_other,
            additional_message=additional_message,
            created_by=current_user.id,
            created_by_name=current_user.fullname,
        )

    return stop


def get_collector_schedules(
    db: Session,
    collector: User,
    target_date: Optional[date] = None,
    status: Optional[str] = None,
) -> List[CollectionSchedule]:
    """Return schedules assigned to the given Collector account."""
    clauses = [CollectionSchedule.assigned_collector_id == collector.id]
    if collector.fullname:
        clauses.append(CollectionSchedule.assigned_personnel.ilike(collector.fullname.strip()))

    query = db.query(CollectionSchedule).filter(or_(*clauses))
    if target_date:
        query = query.filter(CollectionSchedule.collection_date == target_date)
    if status:
        query = query.filter(CollectionSchedule.status == status)

    schedules = _load_with_stops(query).order_by(
        CollectionSchedule.collection_date.desc(),
        CollectionSchedule.collection_time.asc(),
    ).all()
    return apply_derived_statuses(schedules)


def get_collector_dashboard_summary(db: Session, collector: User) -> dict:
    """Return operational summary metrics and today/upcoming schedules for a Collector."""
    today = ph_today()
    all_assigned = get_collector_schedules(db, collector)

    today_schedules = [s for s in all_assigned if s.collection_date == today]
    upcoming_schedules = [
        s for s in all_assigned
        if s.collection_date > today and s.status not in ("Completed", "Cancelled")
    ]
    upcoming_schedules.sort(key=lambda s: (s.collection_date, s.collection_time or time(0, 0)))

    total_stops_today = 0
    completed_stops_today = 0
    pending_stops_today = 0
    delayed_stops_today = 0
    cancelled_stops_today = 0

    for s in today_schedules:
        stops = s.stops or []
        for st in stops:
            total_stops_today += 1
            st_status = (st.status or "Upcoming").strip()
            if st_status == "Completed":
                completed_stops_today += 1
            elif st_status == "Delayed":
                delayed_stops_today += 1
            elif st_status == "Cancelled":
                cancelled_stops_today += 1
            else:
                pending_stops_today += 1

    completed_routes_total = sum(1 for s in all_assigned if s.status == "Completed")

    return {
        "collector": {
            "id": collector.id,
            "fullname": collector.fullname,
            "email": collector.email,
            "phone": collector.phone,
            "barangay": collector.barangay,
            "role": collector.role,
        },
        "today_date": str(today),
        "total_routes_today": len(today_schedules),
        "total_stops_today": total_stops_today,
        "completed_stops_today": completed_stops_today,
        "pending_stops_today": pending_stops_today,
        "delayed_stops_today": delayed_stops_today,
        "cancelled_stops_today": cancelled_stops_today,
        "total_assigned_routes": len(all_assigned),
        "completed_routes_total": completed_routes_total,
        "today_schedules": today_schedules,
        "upcoming_schedules": upcoming_schedules[:10],
    }


def get_route_preview_data(db: Session, target_date: date) -> dict:
    """Get all assignments for target_date with nested stops and independent stop times."""
    schedules = get_schedules_by_date(db, target_date)
    assignments = []
    for s in schedules:
        stops_list = []
        if s.stops:
            for st in sorted(s.stops, key=lambda x: x.sequence):
                stops_list.append({
                    "id": st.id,
                    "barangay": st.barangay,
                    "street": st.street or "",
                    "sequence": st.sequence,
                    "collection_time": st.collection_time or s.collection_time,
                    "status": st.status or s.status or "Upcoming",
                    "arriving_at": getattr(st, "arriving_at", None),
                    "arrived_at": getattr(st, "arrived_at", None),
                    "completed_at": getattr(st, "completed_at", None),
                    "delayed_at": getattr(st, "delayed_at", None),
                    "cancelled_at": getattr(st, "cancelled_at", None),
                    "delay_reason": getattr(st, "delay_reason", None),
                    "cancellation_reason": getattr(st, "cancellation_reason", None),
                })
        else:
            stops_list.append({
                "id": s.id,
                "barangay": s.barangay,
                "street": s.zone or "",
                "sequence": 1,
                "collection_time": s.collection_time,
                "status": s.status or "Upcoming",
            })

        assignments.append({
            "id": s.id,
            "truck_number": s.truck_number or "",
            "assigned_personnel": s.assigned_personnel,
            "assigned_collector_id": getattr(s, "assigned_collector_id", None),
            "route_name": s.route_name or f"{s.barangay} Route",
            "starting_point": s.starting_point or s.zone or f"{s.barangay} Center",
            "collection_date": s.collection_date,
            "collection_time": s.collection_time,
            "status": s.status,
            "remarks": s.remarks,
            "stops": stops_list
        })

    return {
        "date": target_date,
        "total_assignments": len(assignments),
        "assignments": assignments
    }


def add_stop_to_schedule(
    db: Session,
    schedule_id: int,
    barangay: str,
    street: str = "",
    collection_time: Optional[time] = None,
    sequence: Optional[int] = None
):
    schedule = get_collection_schedule_by_id(db, schedule_id)
    if not schedule:
        return None
    if sequence is None:
        max_seq = max([s.sequence for s in schedule.stops], default=0)
        sequence = max_seq + 1

    stop = CollectionStop(
        schedule_id=schedule_id,
        barangay=barangay,
        street=street,
        sequence=sequence,
        collection_time=collection_time or schedule.collection_time,
        status="Upcoming"
    )
    db.add(stop)
    db.commit()
    db.refresh(stop)
    return stop


def update_stop(db: Session, stop_id: int, stop_data: dict):
    stop = db.query(CollectionStop).filter(CollectionStop.id == stop_id).first()
    if not stop:
        return None
    for k, v in stop_data.items():
        if v is not None:
            setattr(stop, k, v)
    db.commit()
    db.refresh(stop)
    return stop


def delete_stop(db: Session, stop_id: int):
    stop = db.query(CollectionStop).filter(CollectionStop.id == stop_id).first()
    if not stop:
        return False
    db.delete(stop)
    db.commit()
    return True


def get_stop_by_id(db: Session, stop_id: int):
    return db.query(CollectionStop).filter(CollectionStop.id == stop_id).first()
