from sqlalchemy import Column, Integer, String, Text
from apps.database import Base

class AIGuide(Base):
    __tablename__ = "ai_guides"
    
    id = Column(Integer, primary_key=True, index=True)
    waste_type = Column(String(50), nullable=False, unique=True)
    guide_title = Column(String(255), nullable=False)
    guide_description = Column(Text)
    disposal_method = Column(Text)
    recycling_tips = Column(Text)
    image_url = Column(String(255))
