from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import date, time, datetime


# ──────────────── Stop Schemas ────────────────────────────────────────────────

class CollectionStopCreate(BaseModel):
    """Data for one collection stop with its own independent collection time."""
    barangay: str
    street: str = ""
    collection_time: Optional[time] = None
    sequence: Optional[int] = None  # auto-assigned if omitted


class CollectionStopUpdate(BaseModel):
    """Partial update for a single stop."""
    barangay: Optional[str] = None
    street: Optional[str] = None
    collection_time: Optional[time] = None
    sequence: Optional[int] = None
    status: Optional[str] = None
    remarks: Optional[str] = None


class CollectionStopResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    schedule_id: int
    barangay: str
    street: str
    sequence: int
    collection_time: Optional[time] = None
    status: str
    remarks: Optional[str] = None
    created_at: Optional[datetime] = None


# ──────────────── Assignment Schemas ─────────────────────────────────────────

class CollectionScheduleCreate(BaseModel):
    """Create one collection assignment with multiple stops."""
    collection_date: date
    collection_time: Optional[time] = None  # if omitted, derived from first stop
    truck_number: Optional[str] = ""
    assigned_personnel: str = ""
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    status: str = "Upcoming"
    remarks: Optional[str] = None
    # Multi-stop list — each stop has its own barangay, street, and collection_time
    stops: List[CollectionStopCreate] = []

    # Legacy single-stop compat: if caller sends barangay/zone/collection_time but no stops array
    barangay: Optional[str] = None
    zone: Optional[str] = None

    @field_validator("stops", mode="before")
    @classmethod
    def at_least_one_stop(cls, v, info):
        """If stops is empty, try to build from legacy barangay/zone fields."""
        if not v:
            data = info.data if hasattr(info, "data") else {}
            barangay = data.get("barangay") or ""
            zone = data.get("zone") or ""
            ctime = data.get("collection_time")
            if barangay:
                return [{"barangay": barangay, "street": zone, "collection_time": ctime, "sequence": 1}]
        return v


class BatchScheduleCreate(BaseModel):
    """Create multiple assignments for the same day in one call."""
    schedules: List[CollectionScheduleCreate]


class CollectionScheduleUpdate(BaseModel):
    """Partial update of an assignment. Pass stops to replace the whole stop list."""
    barangay: Optional[str] = None           # legacy compat
    zone: Optional[str] = None               # legacy compat
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    truck_number: Optional[str] = None
    collection_date: Optional[date] = None
    collection_time: Optional[time] = None
    assigned_personnel: Optional[str] = None
    status: Optional[str] = None
    remarks: Optional[str] = None
    reason: Optional[str] = None
    reason_other: Optional[str] = None
    additional_message: Optional[str] = None
    # When provided, replaces the full stop list (with per-stop times)
    stops: Optional[List[CollectionStopCreate]] = None


class ScheduleStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None
    reason_other: Optional[str] = None
    additional_message: Optional[str] = None


class CollectionScheduleListItem(BaseModel):
    """Lightweight assignment schema for list / calendar views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    barangay: str         # first stop barangay (for display)
    zone: str             # first stop street (for display)
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    truck_number: Optional[str] = ""
    collection_date: date
    collection_time: time
    assigned_personnel: str
    status: str
    stops: List[CollectionStopResponse] = []


class CollectionScheduleResponse(BaseModel):
    """Full assignment schema including all stops with individual times."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    barangay: str
    zone: str
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    truck_number: Optional[str] = ""
    collection_date: date
    collection_time: time
    assigned_personnel: str
    status: str
    remarks: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    stops: List[CollectionStopResponse] = []


class CollectionScheduleListResponse(BaseModel):
    schedules: List[CollectionScheduleListItem]
    total: int
    page: int
    page_size: int


# ──────────────── Route Preview Schemas ──────────────────────────────────────

class RouteStopInfo(BaseModel):
    """One stop within a RoutePreview assignment with its own collection_time."""
    id: Optional[int] = None
    barangay: str
    street: str = ""
    sequence: int = 1
    collection_time: Optional[time] = None
    status: str = "Upcoming"


class RoutePreviewAssignment(BaseModel):
    id: int
    truck_number: Optional[str] = ""
    assigned_personnel: str
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    collection_date: date
    collection_time: time
    status: str
    remarks: Optional[str] = None
    stops: List[RouteStopInfo] = []


class RoutePreviewResponse(BaseModel):
    date: date
    total_assignments: int
    assignments: List[RoutePreviewAssignment]
