from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import date, time, datetime

class CollectionScheduleCreate(BaseModel):
    barangay: str
    zone: str = ""
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    collection_date: date
    collection_time: time
    assigned_personnel: str = ""
    status: str = "Upcoming"
    remarks: Optional[str] = None

class BatchScheduleCreate(BaseModel):
    schedules: list[CollectionScheduleCreate]

class CollectionScheduleUpdate(BaseModel):
    barangay: Optional[str] = None
    zone: Optional[str] = None
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    collection_date: Optional[date] = None
    collection_time: Optional[time] = None
    assigned_personnel: Optional[str] = None
    status: Optional[str] = None
    remarks: Optional[str] = None
    reason: Optional[str] = None
    reason_other: Optional[str] = None

class ScheduleStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None
    reason_other: Optional[str] = None
    additional_message: Optional[str] = None


class CollectionScheduleListItem(BaseModel):
    """Lightweight schedule schema for list views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    barangay: str
    zone: str
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    collection_date: date
    collection_time: time
    assigned_personnel: str
    status: str


class CollectionScheduleResponse(BaseModel):
    """Full schedule schema for detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    barangay: str
    zone: str
    route_name: Optional[str] = None
    starting_point: Optional[str] = None
    collection_date: date
    collection_time: time
    assigned_personnel: str
    status: str
    remarks: Optional[str]
    created_at: datetime
    updated_at: datetime

class CollectionScheduleListResponse(BaseModel):
    schedules: list[CollectionScheduleListItem]
    total: int
    page: int
    page_size: int
