from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from apps.database import get_db
from apps.schemas.ai_guide import (
    AIGuideCreate,
    AIGuideUpdate,
    AIGuideResponse,
    AIGuideListResponse
)
from apps.services.ai_guide import (
    get_ai_guides,
    get_ai_guide_by_id,
    create_ai_guide,
    update_ai_guide,
    delete_ai_guide
)
from apps.utils.jwt import get_current_admin
from apps.models.user import User

router = APIRouter()

@router.get("/", response_model=AIGuideListResponse)
def list_ai_guides(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    waste_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all AI guides with filtering and pagination."""
    skip = (page - 1) * page_size
    result = get_ai_guides(
        db,
        skip=skip,
        limit=page_size,
        search=search,
        waste_type=waste_type
    )
    
    return {
        "guides": result["guides"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }

@router.get("/{guide_id}", response_model=AIGuideResponse)
def get_ai_guide(
    guide_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get a specific AI guide by ID."""
    guide = get_ai_guide_by_id(db, guide_id)
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI guide not found"
        )
    return guide

@router.post("/", response_model=AIGuideResponse, status_code=status.HTTP_201_CREATED)
def create_new_ai_guide(
    guide_data: AIGuideCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create a new AI guide."""
    guide = create_ai_guide(db, guide_data.model_dump())
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Waste type already exists"
        )
    return guide

@router.put("/{guide_id}", response_model=AIGuideResponse)
def update_existing_ai_guide(
    guide_id: int,
    guide_data: AIGuideUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Update an existing AI guide."""
    guide = update_ai_guide(db, guide_id, guide_data.model_dump(exclude_unset=True))
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI guide not found or waste type already exists"
        )
    return guide

@router.delete("/{guide_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_ai_guide(
    guide_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Delete an AI guide."""
    guide = delete_ai_guide(db, guide_id)
    if not guide:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI guide not found"
        )
    return None
