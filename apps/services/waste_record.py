from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import Optional
from apps.models.waste_record import WasteRecord
from apps.models.user import User


def _apply_waste_record_filters(query, search, waste_type, disposal_category, user_id, start_date, end_date):
    """Reusable filter logic shared by count and data queries."""
    if search:
        query = query.filter(
            or_(
                WasteRecord.waste_type.ilike(f"%{search}%"),
                User.fullname.ilike(f"%{search}%"),
                WasteRecord.disposal_category.ilike(f"%{search}%")
            )
        )
    if waste_type:
        query = query.filter(WasteRecord.waste_type == waste_type)
    if disposal_category:
        query = query.filter(WasteRecord.disposal_category == disposal_category)
    if user_id:
        query = query.filter(WasteRecord.user_id == user_id)
    if start_date:
        query = query.filter(WasteRecord.classified_at >= start_date)
    if end_date:
        query = query.filter(WasteRecord.classified_at <= end_date)
    return query


def get_waste_records(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    waste_type: Optional[str] = None,
    disposal_category: Optional[str] = None,
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get waste records with filtering, search, and pagination.
    Joins with users table to include fullname.
    Only selects essential columns for list views — no image data.
    """
    # Separate count query — avoids joining User table just for COUNT
    count_query = db.query(func.count(WasteRecord.id))
    if search:
        count_query = count_query.join(User, WasteRecord.user_id == User.id)
    count_query = _apply_waste_record_filters(
        count_query, search, waste_type, disposal_category, user_id, start_date, end_date
    )
    total = count_query.scalar() or 0

    # Data query — joins only when needed and selects minimal columns
    data_query = db.query(
        WasteRecord.id,
        WasteRecord.waste_type,
        WasteRecord.disposal_category.label('classification'),
        WasteRecord.classified_at.label('created_at'),
        User.fullname
    ).join(User, WasteRecord.user_id == User.id)
    data_query = _apply_waste_record_filters(
        data_query, search, waste_type, disposal_category, user_id, start_date, end_date
    )
    rows = data_query.order_by(WasteRecord.classified_at.desc()).offset(skip).limit(limit).all()

    # Convert Row tuples to dicts for the response schema
    records = [
        {
            "id": row.id,
            "fullname": row.fullname,
            "waste_type": row.waste_type,
            "classification": row.classification,
            "created_at": row.created_at
        }
        for row in rows
    ]

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
