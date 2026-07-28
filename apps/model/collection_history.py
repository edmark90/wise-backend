from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.sql import func
from apps.database import Base

class CollectionHistory(Base):
    __tablename__ = "collection_history"
    
    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, nullable=False)
    personnel_id = Column(Integer)
    collection_date = Column(DateTime, nullable=False)
    area = Column(String(255), nullable=False)
    waste_collected_kg = Column(Float)
    completion_date = Column(DateTime)
    remarks = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
