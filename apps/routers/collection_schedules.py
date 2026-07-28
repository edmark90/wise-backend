from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from apps.database import get_db
from apps.schemas.collection_schedule import (
    CollectionScheduleCreate,
    CollectionScheduleUpdate,
    CollectionScheduleResponse,
    CollectionScheduleListResponse
)
from apps.services.collection_schedule_service import (
    get_collection_schedules,
    get_collection_schedule_by_id,
    create_collection_schedule,
    update_collection_schedule,
    delete_collection_schedule
)
from apps.utils.jwt import get_current_admin
from apps.model.user import User

router = APIRouter()

@router.get("/", response_model=CollectionScheduleListResponse)
def list_collection_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[str] = None,
    personnel_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all collection schedules with filtering and pagination."""
    skip = (page - 1) * page_size
    result = get_collection_schedules(
        db,
        skip=skip,
        limit=page_size,
        status=status,
        personnel_id=personnel_id
    )
    
    return {
        "schedules": result["schedules"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }

@router.get("/{schedule_id}", response_model=CollectionScheduleResponse)
def get_collection_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get a specific collection schedule by ID."""
    schedule = get_collection_schedule_by_id(db, schedule_id)
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection schedule not found"
        )
    return schedule

@router.post("/", response_model=CollectionScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_new_collection_schedule(
    schedule_data: CollectionScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create a new collection schedule."""
    schedule = create_collection_schedule(db, schedule_data.model_dump())
    return schedule

@router.put("/{schedule_id}", response_model=CollectionScheduleResponse)
def update_existing_collection_schedule(
    schedule_id: int,
    schedule_data: CollectionScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Update an existing collection schedule."""
    schedule = update_collection_schedule(db, schedule_id, schedule_data.model_dump(exclude_unset=True))
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection schedule not found"
        )
    return schedule

@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_collection_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Delete a collection schedule."""
    schedule = delete_collection_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection schedule not found"
        )
    return None
