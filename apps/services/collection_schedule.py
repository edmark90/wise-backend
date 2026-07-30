from sqlalchemy.orm import Session, load_only
from sqlalchemy import or_
from typing import Optional
from datetime import date
from apps.models.collection_schedule import CollectionSchedule

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
                CollectionSchedule.assigned_personnel.ilike(f"%{search}%")
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
    
    return {"schedules": schedules, "total": total}

def get_schedules_by_date(db: Session, target_date: date):
    """Get all schedules for a specific date (calendar view)."""
    return db.query(CollectionSchedule).options(
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
        CollectionSchedule.collection_date == target_date
    ).order_by(CollectionSchedule.collection_time.asc()).all()

def get_schedules_by_date_range(db: Session, start_date: date, end_date: date):
    """Get schedules within a date range (calendar / mobile view)."""
    return db.query(CollectionSchedule).options(
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
    ).order_by(CollectionSchedule.collection_date.asc(), CollectionSchedule.collection_time.asc()).all()

def get_collection_schedule_by_id(db: Session, schedule_id: int):
    """Get a collection schedule by ID."""
    return db.query(CollectionSchedule).filter(CollectionSchedule.id == schedule_id).first()

def create_collection_schedule(db: Session, schedule_data: dict):
    """Create a new collection schedule."""
    db_schedule = CollectionSchedule(**schedule_data)
    db.add(db_schedule)
    db.commit()
    db.refresh(db_schedule)
    return db_schedule

def update_collection_schedule(db: Session, schedule_id: int, schedule_data: dict):
    """Update a collection schedule."""
    db_schedule = get_collection_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None
    
    for key, value in schedule_data.items():
        if value is not None:
            setattr(db_schedule, key, value)
    
    db.commit()
    db.refresh(db_schedule)
    return db_schedule

def delete_collection_schedule(db: Session, schedule_id: int):
    """Delete a collection schedule."""
    db_schedule = get_collection_schedule_by_id(db, schedule_id)
    if not db_schedule:
        return None
    
    db.delete(db_schedule)
    db.commit()
    return db_schedule
