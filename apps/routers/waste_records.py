from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from apps.database import get_db
from apps.schemas.waste_record import (
    WasteRecordCreate,
    WasteRecordUpdate,
    WasteRecordResponse,
    WasteRecordListResponse
)
from apps.services.waste_record import (
    get_waste_records,
    get_waste_record_by_id,
    create_waste_record,
    update_waste_record,
    delete_waste_record
)
from apps.utils.jwt import get_current_admin
from apps.models.user import User

router = APIRouter()

@router.get("/", response_model=WasteRecordListResponse)
def list_waste_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    waste_type: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all waste records with filtering and pagination."""
    skip = (page - 1) * page_size
    result = get_waste_records(
        db,
        skip=skip,
        limit=page_size,
        waste_type=waste_type,
        status=status,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return {
        "records": result["records"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }

@router.get("/{record_id}", response_model=WasteRecordResponse)
def get_waste_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get a specific waste record by ID."""
    record = get_waste_record_by_id(db, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waste record not found"
        )
    return record

@router.post("/", response_model=WasteRecordResponse, status_code=status.HTTP_201_CREATED)
def create_new_waste_record(
    record_data: WasteRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create a new waste record."""
    record = create_waste_record(db, record_data.model_dump())
    return record

@router.put("/{record_id}", response_model=WasteRecordResponse)
def update_existing_waste_record(
    record_id: int,
    record_data: WasteRecordUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Update an existing waste record."""
    record = update_waste_record(db, record_id, record_data.model_dump(exclude_unset=True))
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waste record not found"
        )
    return record

@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_existing_waste_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Delete a waste record."""
    record = delete_waste_record(db, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Waste record not found"
        )
    return None
