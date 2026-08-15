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
    is_flagged: Optional[bool] = None


class WasteRecordListItem(BaseModel):
    """Lightweight waste record schema for list views - includes confidence and image path."""
    id: int
    fullname: str
    waste_type: str
    classification: str  # maps to disposal_category in DB
    confidence: Optional[float] = None
    image_url: Optional[str] = None
    barangay: Optional[str] = None
    zone: Optional[str] = None
    is_flagged: bool = False
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


class WasteClassificationStats(BaseModel):
    """Aggregates that feed the admin Waste Classification dashboard cards."""
    total_records: int
    records_last_24h: int
    records_prior_24h: int
    trend_24h_pct: float
    records_today: int
    avg_confidence: Optional[float] = None
    avg_confidence_last_24h: Optional[float] = None
    avg_confidence_prior_24h: Optional[float] = None
    avg_confidence_trend_pct: Optional[float] = None
    most_common_type: Optional[str] = None
    most_common_count: int = 0
    most_common_share_pct: float = 0.0
    per_class: dict[str, int] = {}
