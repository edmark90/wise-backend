from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class CollectionHistoryResponse(BaseModel):
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
    history: list[CollectionHistoryResponse]
    total: int
    page: int
    page_size: int
