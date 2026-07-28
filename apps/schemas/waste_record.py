from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class WasteRecordCreate(BaseModel):
    user_id: int
    waste_type: str
    image_url: Optional[str] = None
    confidence_score: Optional[float] = None
    location: Optional[str] = None
    status: str = "pending"

class WasteRecordUpdate(BaseModel):
    waste_type: Optional[str] = None
    image_url: Optional[str] = None
    confidence_score: Optional[float] = None
    location: Optional[str] = None
    status: Optional[str] = None

class WasteRecordResponse(BaseModel):
    id: int
    user_id: int
    waste_type: str
    image_url: Optional[str]
    confidence_score: Optional[float]
    location: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

class WasteRecordListResponse(BaseModel):
    records: list[WasteRecordResponse]
    total: int
    page: int
    page_size: int
