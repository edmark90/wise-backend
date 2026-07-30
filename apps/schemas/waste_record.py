from pydantic import BaseModel, ConfigDict, field_serializer
from typing import Optional
from decimal import Decimal
from datetime import datetime

class WasteRecordCreate(BaseModel):
    user_id: int
    image_url: str
    waste_type: str
    disposal_category: str
    confidence: Optional[Decimal] = None

class WasteRecordUpdate(BaseModel):
    image_url: Optional[str] = None
    waste_type: Optional[str] = None
    disposal_category: Optional[str] = None
    confidence: Optional[Decimal] = None


class WasteRecordListItem(BaseModel):
    """Lightweight waste record schema for list views - no image_url or confidence."""
    id: int
    fullname: str
    waste_type: str
    classification: str  # maps to disposal_category in DB
    created_at: datetime  # maps to classified_at in DB


class WasteRecordResponse(BaseModel):
    """Full waste record schema for detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    image_url: str
    waste_type: str
    disposal_category: str
    confidence: Optional[Decimal] = None
    classified_at: Optional[datetime] = None

    @field_serializer('classified_at')
    def serialize_classified_at(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat()


class WasteRecordListResponse(BaseModel):
    records: list[WasteRecordListItem]
    total: int
    page: int
    page_size: int
