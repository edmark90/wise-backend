"""Shared road-route geometry for the website preview and the citizen app.

One OpenRouteService result is saved per exact ordered coordinate chain and is
reused by every later viewer. A chain only changes when an admin edits a route
or moves a stop, so citizens opening schedules never spend Directions quota.

All geometry stays on the backend; the ORS API key is never exposed to either
client.
"""

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, time
from hashlib import sha256
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.database import Base, engine
from apps.models.route_geometry import RouteGeometry

# Verified HeiGIT endpoint (the old api.openrouteservice.org host is retired).
ORS_DIRECTIONS_URL = (
    "https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson"
)
ORS_PROFILE = "driving-car"
ORS_TIMEOUT_SECONDS = 15

# Dispatch depot: Arandia Street, Barangay Tunasan, Muntinlupa City.
# Leaflet-style [latitude, longitude]; ORS coordinates are converted on the way out.
DEPOT = {
    "name": "Tunasan Dispatch Depot",
    "address": "Arandia Street, Barangay Tunasan, Muntinlupa City",
    "coord": (14.384538, 121.051391),
}

LatLng = Tuple[float, float]

_table_ready = False
_table_lock = threading.Lock()


class RoutingUnavailable(Exception):
    """Raised when live road geometry cannot be calculated right now."""


class RoutingConfigError(RoutingUnavailable):
    """Raised when the backend has no OpenRouteService key configured."""


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def ensure_geometry_table() -> None:
    """Create the cache table on first use (idempotent, no startup migration)."""
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return
        Base.metadata.create_all(
            bind=engine, tables=[RouteGeometry.__table__], checkfirst=True
        )
        _table_ready = True


def chain_hash(coordinates: Sequence[Sequence[float]]) -> str:
    """Stable hash of an ordered [longitude, latitude] chain for the driving profile."""
    canonical = [
        [round(float(c[0]), 6), round(float(c[1]), 6)] for c in coordinates
    ]
    raw = json.dumps(canonical, separators=(",", ":")) + "|" + ORS_PROFILE
    return sha256(raw.encode("utf-8")).hexdigest()


def get_cached_geometry(db: Optional[Session], key: str) -> Optional[RouteGeometry]:
    if db is None:
        return None
    try:
        ensure_geometry_table()
        return (
            db.query(RouteGeometry)
            .filter(RouteGeometry.chain_hash == key)
            .first()
        )
    except SQLAlchemyError as exc:  # cache must never break the preview
        db.rollback()
        print(f"[route-geometry] cache read skipped: {exc}")
        return None


def save_geometry(
    db: Optional[Session],
    key: str,
    ors_geojson: dict,
    waypoints: Sequence[Sequence[float]],
    schedule_id: Optional[int] = None,
) -> Optional[RouteGeometry]:
    """Persist a successful ORS result, reusing the row if another request won the race."""
    if db is None:
        return None
    try:
        ensure_geometry_table()
        existing = (
            db.query(RouteGeometry)
            .filter(RouteGeometry.chain_hash == key)
            .first()
        )
        if existing:
            return existing

        latlngs = _latlngs_from_geojson(ors_geojson)
        legs = split_route_into_legs(latlngs, waypoints) if latlngs else None
        summary = _summary_from_geojson(ors_geojson) or {}

        row = RouteGeometry(
            chain_hash=key,
            schedule_id=schedule_id,
            ors_response=json.dumps(ors_geojson),
            legs=json.dumps(_legs_to_geojson(legs)) if legs else None,
            distance_meters=_int_or_none(summary.get("distance")),
            duration_seconds=_int_or_none(summary.get("duration")),
            provider="openrouteservice",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except SQLAlchemyError as exc:  # losing the cache is fine; the response is not
        db.rollback()
        print(f"[route-geometry] cache write skipped: {exc}")
        return None


# ---------------------------------------------------------------------------
# OpenRouteService
# ---------------------------------------------------------------------------

def fetch_ors_geojson(
    coordinates: Sequence[Sequence[float]],
    timeout: int = ORS_TIMEOUT_SECONDS,
) -> dict:
    """Call the HeiGIT Directions API with the backend-only key."""
    key = os.getenv("OPENROUTESERVICE_API_KEY", "").strip()
    if not key:
        raise RoutingConfigError(
            "Routing service is not configured. Contact the administrator."
        )

    body = json.dumps({"coordinates": list(coordinates)}).encode("utf-8")
    req = urllib.request.Request(ORS_DIRECTIONS_URL, data=body, method="POST")
    req.add_header("Authorization", key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, application/geo+json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RoutingUnavailable(
            f"Routing service error ({exc.code}): {detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RoutingUnavailable(
            f"Could not reach routing service: {exc.reason}"
        ) from exc
    except Exception as exc:  # malformed response / timeout
        raise RoutingUnavailable(f"Routing proxy error: {exc}") from exc


def get_or_create_geometry(
    db: Optional[Session],
    coordinates: Sequence[Sequence[float]],
    schedule_id: Optional[int] = None,
) -> dict:
    """Return saved ORS GeoJSON for this chain, calculating it only on a miss."""
    key = chain_hash(coordinates)
    cached = get_cached_geometry(db, key)
    if cached is not None:
        try:
            return json.loads(cached.ors_response)
        except (TypeError, ValueError):
            # Drop a corrupt row so the fresh result can replace it.
            try:
                db.delete(cached)
                db.commit()
            except SQLAlchemyError:
                db.rollback()

    ors_geojson = fetch_ors_geojson(coordinates)
    # Waypoints for leg splitting are internally [lat, lng], like the geometry.
    latlng_waypoints = [(float(c[1]), float(c[0])) for c in coordinates]
    save_geometry(db, key, ors_geojson, latlng_waypoints, schedule_id=schedule_id)
    return ors_geojson


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def _feature_from_geojson(payload: dict) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    features = payload.get("features")
    if isinstance(features, list) and features:
        return features[0]
    if payload.get("geometry"):
        return payload
    return None


def _latlngs_from_geojson(payload: dict) -> List[LatLng]:
    feature = _feature_from_geojson(payload) or {}
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    if geometry.get("type") != "LineString":
        return []
    try:
        return [(float(c[1]), float(c[0])) for c in coords if len(c) >= 2]
    except (TypeError, ValueError, IndexError):
        return []


def _summary_from_geojson(payload: dict) -> Optional[dict]:
    feature = _feature_from_geojson(payload) or {}
    props = feature.get("properties") or {}
    summary = props.get("summary")
    return summary if isinstance(summary, dict) else None


def _legs_to_geojson(legs: Optional[List[List[LatLng]]]) -> List[List[List[float]]]:
    """Convert [lat, lng] legs to GeoJSON [lng, lat] rings for the mobile client."""
    if not legs:
        return []
    return [[[lng, lat] for (lat, lng) in leg] for leg in legs]


def _int_or_none(value) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _find_nearest_vertex_index(
    latlngs: Sequence[LatLng], target: LatLng, from_index: int
) -> Optional[int]:
    best_idx = None
    best_dist = float("inf")
    for i in range(max(0, from_index), len(latlngs)):
        d_lat = latlngs[i][0] - target[0]
        d_lng = latlngs[i][1] - target[1]
        dist = d_lat * d_lat + d_lng * d_lng
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    if best_idx is None:
        return None
    # ~1.5 km guard: never slice geometry that does not actually pass the stop.
    if best_dist > 0.00018:
        return None
    return best_idx


def split_route_into_legs(
    latlngs: Sequence[LatLng], waypoints: Sequence[LatLng]
) -> Optional[List[List[LatLng]]]:
    """Cut one road polyline into stop-to-stop legs at the scheduled waypoints."""
    if not latlngs or len(latlngs) < 2 or not waypoints or len(waypoints) < 2:
        return None

    cuts = [0]
    cursor = 0
    for w in range(1, len(waypoints) - 1):
        idx = _find_nearest_vertex_index(latlngs, waypoints[w], cursor)
        if idx is None or idx < cursor:
            return None
        cuts.append(idx)
        cursor = idx
    cuts.append(len(latlngs) - 1)

    legs = []
    for i in range(len(cuts) - 1):
        start, end = cuts[i], cuts[i + 1]
        if start == end:
            # Two scheduled stops share one polyline vertex. Emit a two-point
            # leg so the leg count always equals (waypoints - 1): clients map a
            # stop's waypoint_index straight onto this list.
            legs.append([latlngs[start], latlngs[start]])
        else:
            legs.append(list(latlngs[start:end + 1]))
    return legs


# ---------------------------------------------------------------------------
# Schedule payload (shared read model for mobile)
# ---------------------------------------------------------------------------

def _schedule_stops(schedule) -> List[dict]:
    stops = sorted(
        getattr(schedule, "stops", None) or [], key=lambda s: s.sequence or 0
    )
    if stops:
        result = []
        for st in stops:
            when = st.collection_time or schedule.collection_time
            result.append(
                {
                    "id": st.id,
                    "barangay": st.barangay,
                    "street": st.street or "",
                    "sequence": st.sequence or 1,
                    "collection_time": _time_text(when),
                    "status": st.status or schedule.status or "Upcoming",
                }
            )
        return result

    # Legacy assignment without child stops.
    return [
        {
            "id": schedule.id,
            "barangay": schedule.barangay or "",
            "street": schedule.zone or "",
            "sequence": 1,
            "collection_time": _time_text(schedule.collection_time),
            "status": schedule.status or "Upcoming",
        }
    ]


def _time_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return str(value)


def _date_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def build_schedule_route(db: Session, schedule, apply_statuses=None) -> dict:
    """Build the geometry read model for one schedule assignment.

    Never raises for a missing route: an assignment with no resolvable stops or
    a temporarily unavailable routing service still returns its stop list, so
    the app can explain the situation instead of showing a blank map.
    """
    from apps.services.map_coordinates import resolve_stop_coords

    if apply_statuses is not None:
        apply_statuses([schedule])

    warnings: List[str] = []
    stops = _schedule_stops(schedule)

    valid_coords: List[LatLng] = []
    for idx, stop in enumerate(stops):
        resolved = resolve_stop_coords(stop["barangay"], stop["street"])
        coord = resolved["coord"]
        if coord is None:
            stop["latitude"] = None
            stop["longitude"] = None
            stop["is_exact"] = False
            stop["waypoint_index"] = None
            warnings.append(
                f"Stop {idx + 1} ({stop['barangay']}): "
                + (resolved.get("reason") or "Missing coordinates.")
            )
            continue

        stop["latitude"] = coord[0]
        stop["longitude"] = coord[1]
        stop["is_exact"] = bool(resolved["is_exact"])
        valid_coords.append(coord)
        stop["waypoint_index"] = len(valid_coords)  # depot is waypoint 0
        if resolved.get("is_approximate") and resolved.get("reason"):
            warnings.append(f"Stop {idx + 1}: {resolved['reason']}")

    waypoints: List[LatLng] = [DEPOT["coord"]] + valid_coords
    payload = {
        "schedule_id": schedule.id,
        "truck_number": schedule.truck_number or "",
        "route_name": schedule.route_name or f"{schedule.barangay} Route",
        "starting_point": schedule.starting_point or schedule.zone or "",
        "collection_date": _date_text(schedule.collection_date),
        "collection_time": _time_text(schedule.collection_time),
        "status": schedule.status or "Upcoming",
        "depot": {
            "name": DEPOT["name"],
            "address": DEPOT["address"],
            "latitude": DEPOT["coord"][0],
            "longitude": DEPOT["coord"][1],
        },
        "stops": stops,
        "geometry": None,
        "legs": None,
        "is_segmented": False,
        "distance_meters": None,
        "duration_seconds": None,
        "message": None,
        "warnings": warnings,
    }

    if len(waypoints) < 2:
        payload["message"] = (
            "No collection stop has usable coordinates, so a road route cannot be drawn."
        )
        return payload

    coordinates = [[lng, lat] for (lat, lng) in waypoints]
    key = chain_hash(coordinates)
    cached = get_cached_geometry(db, key)

    if cached is not None:
        try:
            payload["geometry"] = (_feature_from_geojson(json.loads(cached.ors_response)) or {}).get(
                "geometry"
            )
            payload["legs"] = json.loads(cached.legs) if cached.legs else None
            payload["distance_meters"] = cached.distance_meters
            payload["duration_seconds"] = cached.duration_seconds
        except (TypeError, ValueError):
            # Corrupt row: drop it so the fresh result can be cached.
            try:
                db.delete(cached)
                db.commit()
            except SQLAlchemyError:
                db.rollback()
            cached = None

    # Older cache rows may hold geometry without stop-to-stop legs, or legs
    # that do not line up 1:1 with the waypoint chain. Split it locally instead
    # of spending another Directions request.
    if (
        cached is not None
        and payload.get("geometry")
        and (
            not payload.get("legs")
            or len(payload["legs"]) != len(waypoints) - 1
        )
    ):
        latlngs = [
            (float(c[1]), float(c[0]))
            for c in payload["geometry"].get("coordinates", [])
            if len(c) >= 2
        ]
        legs = split_route_into_legs(latlngs, waypoints) if latlngs else None
        if legs:
            payload["legs"] = _legs_to_geojson(legs)
            try:
                cached.legs = json.dumps(payload["legs"])
                db.commit()
            except SQLAlchemyError:
                db.rollback()

    if cached is None:
        try:
            ors_geojson = fetch_ors_geojson(coordinates)
        except RoutingUnavailable as exc:
            payload["message"] = (
                "Saved road route is not available and the routing service "
                f"could not be reached ({exc}). Stops are still shown."
            )
            return payload

        latlngs = _latlngs_from_geojson(ors_geojson)
        legs = split_route_into_legs(latlngs, waypoints) if latlngs else None
        payload["geometry"] = (_feature_from_geojson(ors_geojson) or {}).get("geometry")
        payload["legs"] = _legs_to_geojson(legs) if legs else None
        summary = _summary_from_geojson(ors_geojson) or {}
        payload["distance_meters"] = _int_or_none(summary.get("distance"))
        payload["duration_seconds"] = _int_or_none(summary.get("duration"))
        save_geometry(db, key, ors_geojson, waypoints, schedule_id=schedule.id)

    payload["is_segmented"] = bool(payload.get("legs"))
    if not payload["is_segmented"]:
        warnings.append(
            "Road geometry could not be matched to the scheduled stops; "
            "the app will draw direct stop-to-stop lines."
        )
    return payload
