"""
WISE Backend - Routing Proxy
Securely proxies OpenRouteService Directions API calls.
The API key is read from the OPENROUTESERVICE_API_KEY environment variable
and is never sent to or stored in the frontend.
"""

import os
import json
import urllib.request
import urllib.error
from fastapi import APIRouter, Depends, HTTPException, Body
from apps.utils.jwt import get_current_user
from apps.models.user import User

router = APIRouter()

ORS_DIRECTIONS_URL = "https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson"


def _get_ors_key():
    key = os.getenv("OPENROUTESERVICE_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Routing service is not configured. Contact the administrator."
        )
    return key


@router.post("/directions")
def get_route_directions(
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """
    Proxy a road-routing request to OpenRouteService.
    Expected body: {"coordinates": [[lng, lat], [lng, lat], ...]}
    Returns the raw GeoJSON from ORS. API key injected server-side.
    """
    coordinates = payload.get("coordinates")
    if not coordinates or not isinstance(coordinates, list) or len(coordinates) < 2:
        raise HTTPException(status_code=400, detail="At least 2 coordinate pairs required.")

    for coord in coordinates:
        if not (isinstance(coord, (list, tuple)) and len(coord) == 2):
            raise HTTPException(status_code=400, detail="Each coordinate must be [longitude, latitude].")
        lng, lat = float(coord[0]), float(coord[1])
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise HTTPException(status_code=400, detail=f"Invalid coordinate: {coord}")

    ors_key = _get_ors_key()
    ors_body = json.dumps({"coordinates": coordinates}).encode("utf-8")

    req = urllib.request.Request(ORS_DIRECTIONS_URL, data=ors_body, method="POST")
    req.add_header("Authorization", ors_key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, application/geo+json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Routing service error ({exc.code}): {body[:300]}")
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=504, detail=f"Could not reach routing service: {exc.reason}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Routing proxy error: {str(exc)}")
