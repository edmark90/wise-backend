from sqlalchemy import Column, Integer, String, Date, Time, DateTime, Text
from sqlalchemy.sql import func
from apps.database import Base

class CollectionSchedule(Base):
    __tablename__ = "collection_schedule"
    
    id = Column(Integer, primary_key=True, index=True)
    barangay = Column(String(100), nullable=False)
    zone = Column(String(50), nullable=False, default="")
    collection_date = Column(Date, nullable=False)
    collection_time = Column(Time, nullable=False)
    assigned_personnel = Column(String(255), nullable=False, default="")
    status = Column(String(20), default="Upcoming")
    remarks = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
