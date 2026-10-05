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
    RoutePreviewResponse,
    CollectionStopCreate,
    CollectionStopUpdate,
    CollectionStopResponse,
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
    get_route_preview_data,
    add_stop_to_schedule,
    update_stop,
    delete_stop,
    get_stop_by_id,
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
    truck_number: Optional[str] = None,
    personnel: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    skip = (page - 1) * page_size
    result = get_collection_schedules(
        db, skip=skip, limit=page_size, search=search,
        status=status, barangay=barangay,
        truck_number=truck_number, personnel=personnel,
    )
    return {
        "schedules": result["schedules"],
        "total": result["total"],
        "page": page,
        "page_size": page_size
    }


@router.get("/route-preview/", response_model=RoutePreviewResponse)
def get_route_preview_for_date(
    target_date: date = Query(..., description="Target collection date for route preview"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return get_route_preview_data(db, target_date)


@router.get("/by-date/", response_model=list[CollectionScheduleResponse])
def list_schedules_by_date(
    target_date: date = Query(...),
    status: Optional[str] = Query(None),
    truck_number: Optional[str] = Query(None),
    personnel: Optional[str] = Query(None),
    barangay: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return get_schedules_by_date(db, target_date, status=status, truck_number=truck_number,
                                  personnel=personnel, barangay=barangay)


@router.get("/by-date-range/", response_model=list[CollectionScheduleResponse])
def list_schedules_by_date_range(
    start_date: date = Query(...),
    end_date: date = Query(...),
    truck_number: Optional[str] = Query(None),
    personnel: Optional[str] = Query(None),
    barangay: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    return get_schedules_by_date_range(db, start_date, end_date,
                                       truck_number=truck_number, personnel=personnel, barangay=barangay)


@router.get("/mobile/upcoming/", response_model=list[CollectionScheduleResponse])
def list_mobile_upcoming_schedules(
    start_date: date = Query(...),
    end_date: date = Query(...),
    truck_number: Optional[str] = Query(None),
    personnel: Optional[str] = Query(None),
    barangay: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return get_schedules_by_date_range(db, start_date, end_date,
                                       truck_number=truck_number, personnel=personnel, barangay=barangay)


@router.get("/by-personnel/{personnel_name}", response_model=list[CollectionScheduleResponse])
def list_schedules_by_personnel(
    personnel_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return get_schedules_by_date_range(db, date(2000, 1, 1), date(2100, 12, 31), personnel=personnel_name)


@router.get("/by-truck/{truck_number}", response_model=list[CollectionScheduleResponse])
def list_schedules_by_truck(
    truck_number: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return get_schedules_by_date_range(db, date(2000, 1, 1), date(2100, 12, 31), truck_number=truck_number)


# ─── Stop sub-resource endpoints ─────────────────────────────────────────────

@router.get("/{schedule_id}/stops", response_model=list[CollectionStopResponse])
def list_stops_for_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    schedule = get_collection_schedule_by_id(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Collection schedule not found")
    return sorted(schedule.stops, key=lambda s: s.sequence)


@router.post("/{schedule_id}/stops", response_model=CollectionStopResponse, status_code=201)
def add_stop(
    schedule_id: int,
    stop_data: CollectionStopCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    stop = add_stop_to_schedule(
        db, schedule_id,
        stop_data.barangay,
        stop_data.street,
        stop_data.collection_time,
        stop_data.sequence
    )
    if not stop:
        raise HTTPException(status_code=404, detail="Collection schedule not found")
    return stop


@router.put("/stops/{stop_id}", response_model=CollectionStopResponse)
def update_single_stop(
    stop_id: int,
    stop_data: CollectionStopUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    stop = update_stop(db, stop_id, stop_data.model_dump(exclude_unset=True))
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")
    return stop


@router.delete("/stops/{stop_id}", status_code=204)
def delete_single_stop(
    stop_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    stop = get_stop_by_id(db, stop_id)
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")

    schedule = get_collection_schedule_by_id(db, stop.schedule_id)
    if schedule and len(schedule.stops) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last stop of an assignment.")

    delete_stop(db, stop_id)
    return None


@router.post("/batch", response_model=list[CollectionScheduleResponse], status_code=201)
def create_multiple_schedules(
    batch_data: BatchScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    if not batch_data.schedules:
        raise HTTPException(status_code=400, detail="At least one schedule assignment must be provided")

    try:
        schedules_dicts = [s.model_dump() for s in batch_data.schedules]
        return create_collection_schedules_batch(
            db,
            schedules_dicts,
            created_by=current_user.id,
            created_by_name=getattr(current_user, 'fullname', getattr(current_user, 'full_name', ''))
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create batch schedules: {str(e)}")


@router.post("/", response_model=CollectionScheduleResponse, status_code=201)
def create_schedule(
    schedule_data: CollectionScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    try:
        return create_collection_schedule(db, schedule_data.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create schedule: {str(e)}")


@router.get("/{schedule_id}", response_model=CollectionScheduleResponse)
def get_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    schedule = get_collection_schedule_by_id(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Collection schedule not found")
    derived = apply_derived_statuses([schedule])
    return derived[0]


@router.put("/{schedule_id}", response_model=CollectionScheduleResponse)
def update_schedule(
    schedule_id: int,
    schedule_data: CollectionScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    try:
        schedule = update_collection_schedule(
            db,
            schedule_id,
            schedule_data.model_dump(exclude_unset=True),
            created_by=current_user.id,
            created_by_name=getattr(current_user, 'fullname', getattr(current_user, 'full_name', ''))
        )
        if not schedule:
            raise HTTPException(status_code=404, detail="Collection schedule not found")
        derived = apply_derived_statuses([schedule])
        return derived[0]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update schedule: {str(e)}")


@router.post("/{schedule_id}/status", response_model=CollectionScheduleResponse)
def change_schedule_status(
    schedule_id: int,
    status_data: ScheduleStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    try:
        schedule = set_schedule_status(
            db,
            schedule_id,
            status_data.status,
            reason=status_data.reason,
            reason_other=status_data.reason_other,
            additional_message=status_data.additional_message,
            created_by=current_user.id,
            created_by_name=getattr(current_user, 'fullname', getattr(current_user, 'full_name', ''))
        )
        if not schedule:
            raise HTTPException(status_code=404, detail="Collection schedule not found")
        return schedule
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{schedule_id}", status_code=204)
def remove_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    schedule = delete_collection_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Collection schedule not found")
    return None
