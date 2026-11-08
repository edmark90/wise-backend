from sqlalchemy import Column, Integer, String, Time, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from apps.database import Base


class CollectionStop(Base):
    """A single collection stop belonging to a collection schedule assignment."""
    __tablename__ = "collection_stops"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("collection_schedule.id", ondelete="CASCADE"), nullable=False, index=True)
    barangay = Column(String(100), nullable=False)
    street = Column(String(255), nullable=False, default="")
    sequence = Column(Integer, nullable=False, default=1)
    collection_time = Column(Time, nullable=True)
    status = Column(String(30), default="Upcoming")
    remarks = Column(Text, nullable=True)
    arriving_at = Column(DateTime, nullable=True)
    arrived_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    delayed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    delay_reason = Column(Text, nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    reminder_sent = Column(Integer, default=0)
    updated_by_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Back-reference to parent assignment
    schedule = relationship("CollectionSchedule", back_populates="stops")
