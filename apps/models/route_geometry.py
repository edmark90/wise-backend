from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.sql import func
from apps.database import Base

# Route geometry can be long; MEDIUMTEXT avoids MySQL's 64 KB TEXT limit.
_GeometryText = Text().with_variant(MEDIUMTEXT(), "mysql")


class RouteGeometry(Base):
    """Saved road-route geometry for one ordered stop chain.

    Geometry is keyed by a hash of the exact ORS coordinate chain (depot +
    ordered stops), not by the schedule id. That lets the website preview and
    the citizen mobile map share one saved result for the same route, and it
    automatically invalidates when an admin moves or reorders a stop.
    """

    __tablename__ = "route_geometries"

    id = Column(Integer, primary_key=True, index=True)
    chain_hash = Column(String(64), nullable=False, unique=True, index=True)
    schedule_id = Column(Integer, nullable=True, index=True)
    ors_response = Column(_GeometryText, nullable=False)
    legs = Column(_GeometryText, nullable=True)
    distance_meters = Column(Integer, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    provider = Column(String(50), nullable=False, default="openrouteservice")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
