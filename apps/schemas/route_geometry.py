from typing import List, Optional

from pydantic import BaseModel


class DepotInfo(BaseModel):
    name: str
    address: str
    latitude: float
    longitude: float


class RouteStopPosition(BaseModel):
    """A scheduled stop with its resolved map position."""

    id: int
    barangay: str
    street: str = ""
    sequence: int = 1
    collection_time: Optional[str] = None
    status: str = "Upcoming"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_exact: bool = False
    waypoint_index: Optional[int] = None


class LineStringGeometry(BaseModel):
    type: str
    coordinates: List[List[float]]


class ScheduleRouteResponse(BaseModel):
    """Saved route geometry + ordered stops for one schedule assignment."""

    schedule_id: int
    truck_number: str = ""
    route_name: str = ""
    starting_point: str = ""
    collection_date: Optional[str] = None
    collection_time: Optional[str] = None
    status: str = "Upcoming"
    depot: DepotInfo
    stops: List[RouteStopPosition] = []
    geometry: Optional[LineStringGeometry] = None
    legs: Optional[List[List[List[float]]]] = None
    is_segmented: bool = False
    distance_meters: Optional[int] = None
    duration_seconds: Optional[int] = None
    message: Optional[str] = None
    warnings: List[str] = []
