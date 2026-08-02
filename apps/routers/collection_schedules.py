from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
from apps.database import get_db
from apps.schemas.collection_schedule import (
    CollectionScheduleCreate,
    CollectionScheduleUpdate,
    CollectionScheduleResponse,
    CollectionScheduleListResponse,
    ScheduleStatusUpdate,
    BatchScheduleCreate,
)
from apps.services.collection_schedule import (
    get_collection_schedules,
    get_schedules_by_date,
    get_schedules_by_date_range,
    get_collection_schedule_by_id,
    create_collection_schedule,
    create_collection_schedules_batch,
    update_collection_schedule,
    delete_collection_schedule,
    set_schedule_status,
    apply_derived_statuses,
)
from apps.utils.jwt import get_current_admin, get_current_user
from apps.models.user import User

router = APIRouter()

@router.get("/", response_model=CollectionScheduleListResponse)
def list_collection_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = None,
    barangay: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all collection schedules with search, filtering, and pagination.
    Returns only essential fields for list performance.
    """
    skip = (page - 1) * page_size
    result = get_collection_schedules(
        db,
        skip=skip,
        limit=page_size,
        search=search,
        status=status,
        barangay=barangay
    )
    
    return {
        "schedules": result["schedules"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }

@router.get("/by-date/", response_model=list[CollectionScheduleResponse])
def list_schedules_by_date(
    target_date: date = Query(...),
    status: Optional[str] = Query(None, description="Filter by route status (e.g. Delayed, Cancelled)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get all schedules for a specific date, optionally filtered by status.

    The Notification Module uses `status=Delayed` / `status=Cancelled` to
    auto-display affected routes for the selected collection date.
    """
    return get_schedules_by_date(db, target_date, status)

@router.get("/by-date-range/", response_model=list[CollectionScheduleResponse])
def list_schedules_by_date_range(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get schedules within a date range (for calendar display - admin only)."""
    return get_schedules_by_date_range(db, start_date, end_date)

@router.get("/mobile/upcoming/", response_model=list[CollectionScheduleResponse])
def list_mobile_upcoming_schedules(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get upcoming schedules for mobile users (any authenticated user)."""
    return get_schedules_by_date_range(db, start_date, end_date)

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
    apply_derived_statuses([schedule])
    return schedule

@router.post("/{schedule_id}/status", response_model=CollectionScheduleResponse)
def change_schedule_status(
    schedule_id: int,
    status_data: ScheduleStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Set a route's status (Delayed/Cancelled flow) and auto-generate the
    matching notification with the admin-provided reason."""
    try:
        schedule = set_schedule_status(
            db,
            schedule_id,
            status_data.status,
            reason=status_data.reason,
            reason_other=status_data.reason_other,
            additional_message=status_data.additional_message,
            created_by=current_user.id,
            created_by_name=current_user.fullname,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection schedule not found"
        )
    return schedule

@router.post("/batch", response_model=list[CollectionScheduleResponse], status_code=status.HTTP_201_CREATED)
def create_batch_collection_schedules(
    payload: BatchScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create multiple collection routes for the same day in one call.

    The backend saves all routes, then automatically generates a single
    "Upcoming Collection" notification for the day. Recipients are resolved
    automatically from each citizen's preferred barangays.
    """
    return create_collection_schedules_batch(
        db,
        [s.model_dump() for s in payload.schedules],
        created_by=current_user.id,
        created_by_name=current_user.fullname,
    )

@router.post("/", response_model=CollectionScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_new_collection_schedule(
    schedule_data: CollectionScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Create a new collection schedule.

    Automatically generates an "Upcoming Collection" notification for the
    newly created route."""
    schedule = create_collection_schedule(db, schedule_data.model_dump())
    return schedule

@router.put("/{schedule_id}", response_model=CollectionScheduleResponse)
def update_existing_collection_schedule(
    schedule_id: int,
    schedule_data: CollectionScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Update an existing collection schedule. Status changes auto-generate
    notifications (e.g. auto 'Arriving'/'Completed' write-backs)."""
    schedule = update_collection_schedule(
        db,
        schedule_id,
        schedule_data.model_dump(exclude_unset=True),
        created_by=current_user.id,
        created_by_name=current_user.fullname,
    )
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
