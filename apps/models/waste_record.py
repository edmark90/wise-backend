from sqlalchemy import Column, Integer, String, DateTime, Text, Numeric
from sqlalchemy.sql import func
from apps.database import Base

class WasteRecord(Base):
    """Matches actual DB schema: waste_records table."""
    __tablename__ = "waste_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    image_url = Column(Text, nullable=False)
    waste_type = Column(String(100), nullable=False)
    disposal_category = Column(String(50), nullable=False)
    confidence = Column(Numeric(5, 2), nullable=True)
    classified_at = Column(DateTime, server_default=func.now())
