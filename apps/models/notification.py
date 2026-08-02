from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from apps.database import Base

class Notification(Base):
    """Matches actual DB schema: notifications table.

    Broadcast + targeted notifications produced by automatic route status
    changes and manual announcements. Per-user read state lives in the
    `notification_reads` table.
    """
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String(50), nullable=True, default="Manual Announcement")
    category = Column(String(50), nullable=True)
    target = Column(String(100), default="All")
    schedule_id = Column(Integer, nullable=True)
    route_name = Column(String(255), nullable=True)
    starting_point = Column(String(255), nullable=True)
    affected_barangays = Column(Text, nullable=True)
    collection_date = Column(String(20), nullable=True)
    collection_time = Column(String(20), nullable=True)
    assigned_personnel = Column(String(255), nullable=True)
    reason = Column(String(255), nullable=True)
    reason_other = Column(String(255), nullable=True)
    additional_message = Column(Text, nullable=True)
    priority = Column(String(20), default="Normal")
    recipients = Column(Text, nullable=True)
    status = Column(String(20), default="Sent")
    created_by = Column(Integer, nullable=True)
    created_by_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
