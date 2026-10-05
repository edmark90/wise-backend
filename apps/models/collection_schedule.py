from sqlalchemy import Column, Integer, String, Date, Time, DateTime, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from apps.database import Base


class CollectionSchedule(Base):
    __tablename__ = "collection_schedule"

    id = Column(Integer, primary_key=True, index=True)
    barangay = Column(String(100), nullable=False)   # primary/first barangay (legacy compat)
    zone = Column(String(50), nullable=False, default="")   # primary/first street (legacy compat)
    route_name = Column(String(255), nullable=True)
    starting_point = Column(String(255), nullable=True)
    truck_number = Column(String(100), nullable=True, default="")
    collection_date = Column(Date, nullable=False)
    collection_time = Column(Time, nullable=False)
    assigned_personnel = Column(String(255), nullable=False, default="")
    status = Column(String(20), default="Upcoming")
    remarks = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # One-to-many: an assignment has many stops
    stops = relationship(
        "CollectionStop",
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="CollectionStop.sequence",
        lazy="select",
    )
