"""
WISE Backend - Routing

The OSRM directions proxy stays behind the authenticated API so the secret key
is never exposed. Calculated geometry is saved by ordered coordinate chain, so
the website preview and the citizen mobile map reuse one result instead of
spending Directions quota on every view.
"""

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from apps.database import get_db
from apps.models.user import User
from apps.schemas.route_geometry import ScheduleRouteResponse
from apps.services.collection_schedule import (
    apply_derived_statuses,
    get_collection_schedule_by_id,
)
from apps.services.route_geometry import (
    RoutingUnavailable,
    build_schedule_route,
    get_or_create_geometry,
)
from apps.utils.jwt import get_current_user

router = APIRouter()


def _validate_coordinates(coordinates) -> None:
    if not coordinates or not isinstance(coordinates, list) or len(coordinates) < 2:
        raise HTTPException(status_code=400, detail="At least 2 coordinate pairs required.")

    for coord in coordinates:
        if not (isinstance(coord, (list, tuple)) and len(coord) == 2):
            raise HTTPException(
                status_code=400,
                detail="Each coordinate must be [longitude, latitude].",
            )
        lng, lat = float(coord[0]), float(coord[1])
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise HTTPException(status_code=400, detail=f"Invalid coordinate: {coord}")


@router.post("/directions")
def get_route_directions(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Proxy a road-routing request to OSRM.

    Expected body: {"coordinates": [[lng, lat], ...]}
    Returns OSRM-derived GeoJSON. A saved result for the same coordinate chain is
    returned directly; no API key is needed server-side.
    """
    coordinates = payload.get("coordinates")
    _validate_coordinates(coordinates)

    try:
        return get_or_create_geometry(db, coordinates)
    except RoutingUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/schedule-geometry/{schedule_id}", response_model=ScheduleRouteResponse)
def get_schedule_geometry(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saved road geometry + ordered stops for one collection assignment.

    Reads persisted geometry whenever it matches the assignment's current stop
    coordinates; only a changed (or never-calculated) route reaches OSRM.
    """
    schedule = get_collection_schedule_by_id(db, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Collection schedule not found.")

    return build_schedule_route(db, schedule, apply_statuses=apply_derived_statuses)
