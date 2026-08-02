from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.sql import func

from apps.database import Base

class User(Base):
    """Matches actual DB schema: users table."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    fullname = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    phone = Column(String(20))
    role = Column(String(20), default="citizen")
    profile_image = Column(Text, nullable=True)
    barangay = Column(String(100), nullable=True)
    zone = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)

    # Notification preferences (mobile settings)
    notif_collection_updates = Column(Boolean, default=True)
    notif_route_updates = Column(Boolean, default=True)
    notif_announcements = Column(Boolean, default=True)
    notif_emergency_alerts = Column(Boolean, default=True)
    notif_reminders = Column(Boolean, default=True)
    notif_completed_collection = Column(Boolean, default=True)

    # Preferred barangays for notification targeting (JSON list of names).
    # The mobile Notification Settings page lets a citizen subscribe to one or
    # more barangays; delivery is matched against these (never admin-chosen).
    preferred_barangays = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
