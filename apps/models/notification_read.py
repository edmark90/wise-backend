from sqlalchemy import Column, Integer, DateTime
from sqlalchemy.sql import func
from apps.database import Base

class NotificationRead(Base):
    """Per-user read state for notifications."""
    __tablename__ = "notification_reads"

    id = Column(Integer, primary_key=True, index=True)
    notification_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    read_at = Column(DateTime, server_default=func.now())
