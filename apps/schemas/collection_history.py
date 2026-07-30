from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class CollectionHistoryListItem(BaseModel):
    """Lightweight collection history schema for list views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    schedule_id: int
    collection_date: datetime
    area: str
    waste_collected_kg: Optional[float]


class CollectionHistoryResponse(BaseModel):
    """Full collection history schema for detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    schedule_id: int
    personnel_id: Optional[int]
    collection_date: datetime
    area: str
    waste_collected_kg: Optional[float]
    completion_date: Optional[datetime]
    remarks: Optional[str]
    created_at: datetime

class CollectionHistoryListResponse(BaseModel):
    history: list[CollectionHistoryListItem]
    total: int
    page: int
    page_size: int
