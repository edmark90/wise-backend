from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional, List
from datetime import date, datetime, time, timedelta
from apps.models.collection_schedule import CollectionSchedule
from apps.models.collection_stop import CollectionStop
from apps.models.notification import Notification
from apps.services.notification import (
    STATUS_TO_TYPE,
    handle_status_change,
    generate_route_notification,
    generate_reschedule_notification,
)
from apps.utils.ph_time import ph_now, ph_today

MANUAL_STATUSES = ("Cancelled", "Delayed")
AUTO_STATUSES = ("Upcoming", "Arriving", "Arrived", "Completed")
ARRIVING_LEAD_MINUTES = 30
COMPLETED_AFTER_MINUTES = 60


def derive_schedule_status(schedule, now: Optional[datetime] = None) -> str:
    """Compute the server-time-derived status for a single route assignment."""
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
    """Overwrite each schedule's status in-memory (never committed to DB)."""
    if not schedules:
        return schedules
    now = now or ph_now()
    for s in schedules:
        s.status = derive_schedule_status(s, now)
        # Also derive per-stop status if stop has its own collection_time
        if hasattr(s, "stops") and s.stops:
            for st in s.stops:
                if st.status in MANUAL_STATUSES:
                    continue
                stop_time = st.collection_time or s.collection_time
                if not s.collection_date or not stop_time:
                    continue
                if s.collection_date < now.date():
                    st.status = "Completed"
                elif s.collection_date > now.date():
                    st.status = "Upcoming"
                else:
                    stop_dt = datetime.combine(s.collection_date, stop_time)
                    if now < stop_dt - timedelta(minutes=ARRIVING_LEAD_MINUTES):
                        st.status = "Upcoming"
                    elif now < stop_dt:
                        st.status = "Arriving"
                    elif now < stop_dt + timedelta(minutes=COMPLETED_AFTER_MINUTES):
                        st.status = "Arrived"
                    else:
                        st.status = "Completed"
    return schedules


def sync_auto_status_notifications(db: Session) -> int:
    """Materialize Arriving / Arrived / Completed notifications for today."""
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
    """Ensure a create/update payload never stores an automatic status."""
    status = schedule_data.get("status")
    if status in AUTO_STATUSES:
        schedule_data = dict(schedule_data)
        schedule_data["status"] = "Upcoming"
    return schedule_data


def validate_schedule_conflict(
    db: Session,
    collection_date: date,
    collection_time: time,
    truck_number: Optional[str],
    assigned_personnel: Optional[str],
    exclude_id: Optional[int] = None,
):
    """Prevent double-booking the same truck or personnel on the same date and time."""
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
        if assigned_personnel and s.assigned_personnel and s.assigned_personnel.strip().lower() == assigned_personnel.strip().lower():
            time_str = collection_time.strftime("%I:%M %p") if isinstance(collection_time, time) else str(collection_time)
            raise ValueError(
                f"Personnel '{assigned_personnel}' is already assigned to {s.route_name or s.barangay + ' Route'} "
                f"on {collection_date} at {time_str}. Please choose another personnel or time."
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


def create_collection_schedule(db: Session, schedule_data: dict, notify: bool = True):
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

    schedule_data = _populate_schedule_defaults({**schedule_data, "stops": stops_data})
    schedule_data.pop("stops", None)
    schedule_data = _normalize_status(schedule_data)

    validate_schedule_conflict(
        db,
        schedule_data.get("collection_date"),
        schedule_data.get("collection_time"),
        schedule_data.get("truck_number"),
        schedule_data.get("assigned_personnel")
    )

    db_schedule = CollectionSchedule(**schedule_data)
    db.add(db_schedule)
    db.flush()

    _upsert_stops(db, db_schedule, stops_data)
    db.commit()
    db.refresh(db_schedule)

    if notify and db_schedule.status == "Upcoming":
        generate_route_notification(db, db_schedule, "Upcoming Collection")

    return db_schedule


def create_collection_schedules_batch(
    db: Session,
    schedules: List[dict],
    created_by: Optional[int] = None,
    created_by_name: Optional[str] = None,
):
    """Create multiple collection assignments for a day in one call."""
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

        schedule_data = _populate_schedule_defaults({**schedule_data, "stops": stops_data})
        schedule_data.pop("stops", None)
        schedule_data = _normalize_status(schedule_data)

        validate_schedule_conflict(
            db,
            schedule_data.get("collection_date"),
            schedule_data.get("collection_time"),
            schedule_data.get("truck_number"),
            schedule_data.get("assigned_personnel")
        )
        db_schedule = CollectionSchedule(**schedule_data)
        db.add(db_schedule)
        db.flush()
        _upsert_stops(db, db_schedule, stops_data)
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

    target_date = schedule_data.get("collection_date") or db_schedule.collection_date
    target_time = schedule_data.get("collection_time") or (stops_data[0].get("collection_time") if stops_data else db_schedule.collection_time)
    target_truck = schedule_data.get("truck_number") if "truck_number" in schedule_data else db_schedule.truck_number
    target_personnel = schedule_data.get("assigned_personnel") if "assigned_personnel" in schedule_data else db_schedule.assigned_personnel

    validate_schedule_conflict(db, target_date, target_time, target_truck, target_personnel, exclude_id=schedule_id)

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
    schedule.status = status
    db.commit()
    db.refresh(schedule)
    handle_status_change(
        db, schedule, status,
        reason=reason, reason_other=reason_other,
        additional_message=additional_message,
        created_by=created_by, created_by_name=created_by_name
    )
    return schedule


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
                    "status": st.status or s.status or "Upcoming"
                })
        else:
            stops_list.append({
                "id": s.id,
                "barangay": s.barangay,
                "street": s.zone or "",
                "sequence": 1,
                "collection_time": s.collection_time,
                "status": s.status or "Upcoming"
            })

        assignments.append({
            "id": s.id,
            "truck_number": s.truck_number or "",
            "assigned_personnel": s.assigned_personnel,
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
