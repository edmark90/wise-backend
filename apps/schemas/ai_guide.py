from pydantic import BaseModel, ConfigDict
from typing import Optional

class AIGuideCreate(BaseModel):
    waste_type: str
    guide_title: str
    guide_description: Optional[str] = None
    disposal_method: Optional[str] = None
    recycling_tips: Optional[str] = None
    image_url: Optional[str] = None

class AIGuideUpdate(BaseModel):
    waste_type: Optional[str] = None
    guide_title: Optional[str] = None
    guide_description: Optional[str] = None
    disposal_method: Optional[str] = None
    recycling_tips: Optional[str] = None
    image_url: Optional[str] = None

class AIGuideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    waste_type: str
    guide_title: str
    guide_description: Optional[str]
    disposal_method: Optional[str]
    recycling_tips: Optional[str]
    image_url: Optional[str]

class AIGuideListResponse(BaseModel):
    guides: list[AIGuideResponse]
    total: int
    page: int
    page_size: int
