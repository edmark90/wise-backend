from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class CollectionScheduleCreate(BaseModel):
    personnel_id: Optional[int] = None
    collection_date: datetime
    area: str
    status: str = "pending"
    remarks: Optional[str] = None

class CollectionScheduleUpdate(BaseModel):
    personnel_id: Optional[int] = None
    collection_date: Optional[datetime] = None
    area: Optional[str] = None
    status: Optional[str] = None
    remarks: Optional[str] = None

class CollectionScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    personnel_id: Optional[int]
    collection_date: datetime
    area: str
    status: str
    remarks: Optional[str]
    created_at: datetime
    updated_at: datetime

class CollectionScheduleListResponse(BaseModel):
    schedules: list[CollectionScheduleResponse]
    total: int
    page: int
    page_size: int
