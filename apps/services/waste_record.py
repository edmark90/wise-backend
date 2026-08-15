from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import Optional
from datetime import datetime, timedelta
from apps.models.waste_record import WasteRecord
from apps.models.user import User
from apps.utils.ph_time import ph_now


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

    # Data query — joins users for the source (who captured the photo)
    data_query = db.query(
        WasteRecord.id,
        WasteRecord.user_id,
        WasteRecord.waste_type,
        WasteRecord.disposal_category.label('classification'),
        WasteRecord.confidence,
        WasteRecord.image_url,
        WasteRecord.is_flagged,
        WasteRecord.classified_at.label('created_at'),
        User.fullname,
        User.barangay,
        User.zone,
    ).join(User, WasteRecord.user_id == User.id)
    data_query = _apply_waste_record_filters(
        data_query, search, waste_type, disposal_category, user_id, start_date, end_date
    )
    rows = data_query.order_by(WasteRecord.classified_at.desc()).offset(skip).limit(limit).all()

    # Convert Row tuples to dicts for the response schema
    records = [
        {
            "id": row.id,
            "fullname": row.fullname or "—",
            "waste_type": row.waste_type,
            "classification": row.classification,
            "confidence": float(row.confidence) if row.confidence is not None else None,
            "image_url": row.image_url or "",
            "barangay": row.barangay or "",
            "zone": row.zone or "",
            "is_flagged": bool(row.is_flagged),
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


def get_waste_classification_stats(db: Session):
    """Aggregate statistics that feed the admin Waste Classification page.

    All values are computed server-side with SQL aggregates — never loaded
    into Python — so the dashboard stays fast even at scale.
    """
    now = ph_now()
    today_start = datetime.combine(now.date(), datetime.min.time())
    today_end = datetime.combine(now.date(), datetime.max.time())

    # --- Totals ---------------------------------------------------------
    total_records = db.query(func.count(WasteRecord.id)).scalar() or 0

    records_last_24h = db.query(func.count(WasteRecord.id)).filter(
        WasteRecord.classified_at >= now - timedelta(hours=24)
    ).scalar() or 0

    records_prior_24h = db.query(func.count(WasteRecord.id)).filter(
        WasteRecord.classified_at >= now - timedelta(hours=48),
        WasteRecord.classified_at < now - timedelta(hours=24)
    ).scalar() or 0

    records_today = db.query(func.count(WasteRecord.id)).filter(
        WasteRecord.classified_at >= today_start,
        WasteRecord.classified_at <= today_end
    ).scalar() or 0

    # --- Confidence ------------------------------------------------------
    avg_confidence = db.query(func.avg(WasteRecord.confidence)).scalar()
    avg_conf_last = db.query(func.avg(WasteRecord.confidence)).filter(
        WasteRecord.confidence.isnot(None),
        WasteRecord.classified_at >= now - timedelta(hours=24)
    ).scalar()
    avg_conf_prior = db.query(func.avg(WasteRecord.confidence)).filter(
        WasteRecord.confidence.isnot(None),
        WasteRecord.classified_at >= now - timedelta(hours=48),
        WasteRecord.classified_at < now - timedelta(hours=24)
    ).scalar()

    def _pct(cur, prev):
        if cur is None:
            return None
        if prev is None or prev == 0:
            return 0.0
        return round((float(cur) - float(prev)) / float(prev) * 100, 1)

    # --- Most common type + per-class breakdown --------------------------
    class_rows = db.query(
        WasteRecord.waste_type,
        func.count(WasteRecord.id).label("cnt"),
        func.avg(WasteRecord.confidence).label("avg_conf"),
    ).group_by(WasteRecord.waste_type).all()

    per_class = {
        row.waste_type: int(row.cnt)
        for row in class_rows
    }

    most_common_type = None
    most_common_count = 0
    if class_rows:
        top = max(class_rows, key=lambda r: r.cnt)
        most_common_type = top.waste_type
        most_common_count = int(top.cnt)

    most_common_share_pct = 0.0
    if total_records > 0 and most_common_type:
        most_common_share_pct = round(most_common_count / total_records * 100, 1)

    return {
        "total_records": total_records,
        "records_last_24h": records_last_24h,
        "records_prior_24h": records_prior_24h,
        "trend_24h_pct": _pct(records_last_24h, records_prior_24h),
        "records_today": records_today,
        "avg_confidence": round(float(avg_confidence), 1) if avg_confidence is not None else None,
        "avg_confidence_last_24h": round(float(avg_conf_last), 1) if avg_conf_last is not None else None,
        "avg_confidence_prior_24h": round(float(avg_conf_prior), 1) if avg_conf_prior is not None else None,
        "avg_confidence_trend_pct": _pct(avg_conf_last, avg_conf_prior),
        "most_common_type": most_common_type,
        "most_common_count": most_common_count,
        "most_common_share_pct": most_common_share_pct,
        "per_class": per_class,
    }
