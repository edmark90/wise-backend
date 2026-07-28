from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
from apps.model.ai_guide import AIGuide

def get_ai_guides(
    db: Session,
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    waste_type: Optional[str] = None
):
    """Get AI guides with filtering and pagination."""
    query = db.query(AIGuide)
    
    if search:
        query = query.filter(
            or_(
                AIGuide.waste_type.ilike(f"%{search}%"),
                AIGuide.guide_title.ilike(f"%{search}%"),
                AIGuide.guide_description.ilike(f"%{search}%")
            )
        )
    
    if waste_type:
        query = query.filter(AIGuide.waste_type == waste_type)
    
    total = query.count()
    guides = query.order_by(AIGuide.waste_type.asc()).offset(skip).limit(limit).all()
    
    return {"guides": guides, "total": total}

def get_ai_guide_by_id(db: Session, guide_id: int):
    """Get an AI guide by ID."""
    return db.query(AIGuide).filter(AIGuide.id == guide_id).first()

def get_ai_guide_by_waste_type(db: Session, waste_type: str):
    """Get an AI guide by waste type."""
    return db.query(AIGuide).filter(AIGuide.waste_type == waste_type).first()

def create_ai_guide(db: Session, guide_data: dict):
    """Create a new AI guide."""
    # Check if waste type already exists
    existing_guide = get_ai_guide_by_waste_type(db, guide_data["waste_type"])
    if existing_guide:
        return None
    
    db_guide = AIGuide(**guide_data)
    db.add(db_guide)
    db.commit()
    db.refresh(db_guide)
    return db_guide

def update_ai_guide(db: Session, guide_id: int, guide_data: dict):
    """Update an AI guide."""
    db_guide = get_ai_guide_by_id(db, guide_id)
    if not db_guide:
        return None
    
    # If updating waste type, check if it's already taken
    if "waste_type" in guide_data and guide_data["waste_type"] != db_guide.waste_type:
        existing_guide = get_ai_guide_by_waste_type(db, guide_data["waste_type"])
        if existing_guide:
            return None
    
    for key, value in guide_data.items():
        if value is not None:
            setattr(db_guide, key, value)
    
    db.commit()
    db.refresh(db_guide)
    return db_guide

def delete_ai_guide(db: Session, guide_id: int):
    """Delete an AI guide."""
    db_guide = get_ai_guide_by_id(db, guide_id)
    if not db_guide:
        return None
    
    db.delete(db_guide)
    db.commit()
    return db_guide
