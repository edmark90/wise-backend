from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from apps.database import Base

class AppVersion(Base):
    """Registry of mobile app APK releases."""
    __tablename__ = "app_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    version = Column(String(30), unique=True, nullable=False)
    version_code = Column(Integer, unique=True, nullable=False)
    title = Column(String(120), nullable=False)
    release_notes = Column(Text, nullable=True)
    apk_url = Column(String(500), nullable=False)
    is_force = Column(Boolean, default=False, nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    announcements = relationship("AppUpdateAnnouncement", back_populates="app_version")


class AppUpdateAnnouncement(Base):
    """History of broadcast update announcements and background reminder scheduling."""
    __tablename__ = "app_update_announcements"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(120), nullable=False)
    message = Column(Text, nullable=False)
    app_version_id = Column(Integer, ForeignKey("app_versions.id"), nullable=False)
    force = Column("is_force", Boolean, default=False, nullable=False)
    reminder = Column(String(20), default="None", nullable=False)
    reminder_interval_minutes = Column(Integer, default=0, nullable=False)
    next_reminder_at = Column(DateTime, nullable=True, index=True)
    sent_reminders = Column(Integer, default=0, nullable=False)
    max_reminders = Column(Integer, default=3, nullable=False)
    notification_id = Column(Integer, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    app_version = relationship("AppVersion", back_populates="announcements")
