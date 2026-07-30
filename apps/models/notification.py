from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from apps.database import Base

class Notification(Base):
    """Matches actual DB schema: notifications table."""
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    target = Column(String(100), default="All")
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
