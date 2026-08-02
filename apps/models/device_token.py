from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from apps.database import Base

class DeviceToken(Base):
    """Registered push notification device tokens (FCM) per user."""
    __tablename__ = "device_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    token = Column(String(512), nullable=False, unique=True)
    platform = Column(String(20), default="android")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
