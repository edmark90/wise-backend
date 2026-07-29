from sqlalchemy.orm import Session
from typing import Optional
from apps.models.waste_record import WasteRecord

def get_waste_records(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    waste_type: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get waste records with filtering and pagination."""
    query = db.query(WasteRecord)
    
    if waste_type:
        query = query.filter(WasteRecord.waste_type == waste_type)
    
    if status:
        query = query.filter(WasteRecord.status == status)
    
    if user_id:
        query = query.filter(WasteRecord.user_id == user_id)
    
    if start_date:
        query = query.filter(WasteRecord.created_at >= start_date)
    
    if end_date:
        query = query.filter(WasteRecord.created_at <= end_date)
    
    total = query.count()
    records = query.order_by(WasteRecord.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"records": records, "total": total}

def get_waste_record_by_id(db: Session, record_id: int):
    """Get a waste record by ID."""
    return db.query(WasteRecord).filter(WasteRecord.id == record_id).first()

def create_waste_record(db: Session, record_data: dict):
    """Create a new waste record."""
    db_record = WasteRecord(**record_data)
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record

def update_waste_record(db: Session, record_id: int, record_data: dict):
    """Update a waste record."""
    db_record = get_waste_record_by_id(db, record_id)
    if not db_record:
        return None
    
    for key, value in record_data.items():
        if value is not None:
            setattr(db_record, key, value)
    
    db.commit()
    db.refresh(db_record)
    return db_record

def delete_waste_record(db: Session, record_id: int):
    """Delete a waste record."""
    db_record = get_waste_record_by_id(db, record_id)
    if not db_record:
        return None
    
    db.delete(db_record)
    db.commit()
    return db_record
