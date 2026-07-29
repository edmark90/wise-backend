from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.sql import func
from apps.database import Base

class WasteRecord(Base):
    __tablename__ = "waste_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    waste_type = Column(String(50), nullable=False)
    image_url = Column(String(255))
    confidence_score = Column(Float)
    location = Column(String(255))
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
