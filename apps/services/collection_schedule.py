from sqlalchemy.orm import Session
from typing import Optional
from apps.models.collection_schedule import CollectionSchedule

def get_collection_schedules(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    status: Optional[str] = None,
    personnel_id: Optional[int] = None
):
    """Get collection schedules with filtering and pagination."""
    query = db.query(CollectionSchedule)
    
    if status:
        query = query.filter(CollectionSchedule.status == status)
    
    if personnel_id:
        query = query.filter(CollectionSchedule.personnel_id == personnel_id)
    
    total = query.count()
    schedules = query.order_by(CollectionSchedule.collection_date.asc()).offset(skip).limit(limit).all()
    
    return {"schedules": schedules, "total": total}

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
