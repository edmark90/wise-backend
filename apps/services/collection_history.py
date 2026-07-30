from sqlalchemy.orm import Session, load_only
from sqlalchemy import or_
from typing import Optional
from apps.models.collection_history import CollectionHistory

def get_collection_history(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    personnel_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get collection history with search and filters.
    Only selects essential columns for list performance.
    """
    query = db.query(CollectionHistory).options(
        load_only(
            CollectionHistory.id,
            CollectionHistory.schedule_id,
            CollectionHistory.collection_date,
            CollectionHistory.area,
            CollectionHistory.waste_collected_kg
        )
    )
    
    if search:
        query = query.filter(
            or_(
                CollectionHistory.area.ilike(f"%{search}%"),
                CollectionHistory.remarks.ilike(f"%{search}%")
            )
        )
    
    if personnel_id:
        query = query.filter(CollectionHistory.personnel_id == personnel_id)
    
    if start_date:
        query = query.filter(CollectionHistory.collection_date >= start_date)
    
    if end_date:
        query = query.filter(CollectionHistory.collection_date <= end_date)
    
    total = query.count()
    history = query.order_by(CollectionHistory.collection_date.desc()).offset(skip).limit(limit).all()
    
    return {"history": history, "total": total}
