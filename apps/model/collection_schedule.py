from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from apps.database import Base

class CollectionSchedule(Base):
    __tablename__ = "collection_schedule"
    
    id = Column(Integer, primary_key=True, index=True)
    personnel_id = Column(Integer)
    collection_date = Column(DateTime, nullable=False)
    area = Column(String(255), nullable=False)
    status = Column(String(20), default="pending")
    remarks = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
