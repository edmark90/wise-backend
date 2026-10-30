"""Shared Muntinlupa coordinate registry for schedule-based route previews.

This is the backend copy of the registry the website's route preview uses
(``assets/js/components/route-map.js``). It resolves a collection stop to a
[latitude, longitude] pair using the same precedence:

  1. exact barangay + street/landmark match
  2. case-insensitive partial landmark match (insertion order)
  3. barangay centroid fallback (approximate)
  4. unresolved

Keeping the lookup identical is what lets the mobile map reuse the geometry
the website already calculated for the same ordered stop chain.
"""

import re
from typing import Optional, Tuple

Coord = Tuple[float, float]

BARANGAY_COORDS = {
    'Sucat': (14.45698, 121.05316),
    'Buli': (14.4433, 121.05058),
    'Cupang': (14.43788, 121.05032),
    'Alabang': (14.41903, 121.04434),
    'Ayala Alabang': (14.42261, 121.02694),
    'New Alabang Village': (14.42261, 121.02694),
    'Bayanan': (14.41095, 121.05163),
    'Putatan': (14.39497, 121.04483),
    'Poblacion': (14.38923, 121.04491),
    'Tunasan': (14.38011, 121.05148),
}

LANDMARK_COORDS = {
    'Alabang|Alabang Public Market': (14.41928, 121.04453),
    'Alabang|Alabang Market': (14.41928, 121.04453),
    'Alabang|Muntinlupa City Public Market': (14.41928, 121.04453),
    'Alabang|Festival Mall': (14.41673, 121.04078),
    'Alabang|Asian Hospital and Medical Center': (14.41317, 121.04356),
    'Alabang|Alabang Town Center': (14.42423, 121.03209),
    'Alabang|Filinvest City Central Park': (14.4169, 121.03574),
    'Alabang|Filinvest Covered Court': (14.41289, 121.04428),
    'Alabang|Northgate Cyberzone': (14.42508, 121.03895),
    'Alabang|South Station': (14.42132, 121.04278),
    'Alabang|St. Jerome Emiliani and Sta. Susana Parish': (14.42208, 121.03162),
    'Alabang|Saint Jerome Emiliani': (14.42208, 121.03162),
    'Alabang|Zone 1': (14.421, 121.044),
    'Alabang|Zone 2': (14.418, 121.042),
    'New Alabang Village|New Alabang Village (Ayala Alabang)': (14.42261, 121.02694),
    'New Alabang Village|Ayala Alabang Barangay Hall': (14.41832, 121.02435),
    'New Alabang Village|Alabang Country Club': (14.40247, 121.0221),
    'New Alabang Village|The Palms Country Club': (14.41071, 121.0346),
    'New Alabang Village|San James the Great Parish': (14.3916, 121.01412),
    'New Alabang Village|Saint James the Great Parish': (14.3916, 121.01412),
    'New Alabang Village|Molito Lifestyle Center': (14.42432, 121.02677),
    'New Alabang Village|Madrigal Avenue': (14.42306, 121.02707),
    'New Alabang Village|Acacia Avenue': (14.39929, 121.01326),
    'New Alabang Village|Molave Street': (14.41664, 121.02188),
    'New Alabang Village|Narra Street': (14.41258, 121.02033),
    'New Alabang Village|Yakal Street': (14.42178, 121.02569),
    'New Alabang Village|Zone 1': (14.423, 121.027),
    'New Alabang Village|Zone 2': (14.418, 121.024),
    'Ayala Alabang|New Alabang Village (Ayala Alabang)': (14.42261, 121.02694),
    'Ayala Alabang|Ayala Alabang Barangay Hall': (14.41832, 121.02435),
    'Ayala Alabang|Alabang Country Club': (14.40247, 121.0221),
    'Ayala Alabang|The Palms Country Club': (14.41071, 121.0346),
    'Ayala Alabang|San James the Great Parish': (14.3916, 121.01412),
    'Ayala Alabang|Saint James the Great Parish': (14.3916, 121.01412),
    'Ayala Alabang|Molito Lifestyle Center': (14.42432, 121.02677),
    'Ayala Alabang|Madrigal Avenue': (14.42306, 121.02707),
    'Ayala Alabang|Acacia Avenue': (14.39929, 121.01326),
    'Ayala Alabang|Molave Street': (14.41664, 121.02188),
    'Ayala Alabang|Narra Street': (14.41258, 121.02033),
    'Ayala Alabang|Yakal Street': (14.42178, 121.02569),
    'Ayala Alabang|Zone 1': (14.423, 121.027),
    'Ayala Alabang|Zone 2': (14.418, 121.024),
    'Bayanan|Bayanan Barangay Hall': (14.41141, 121.0513),
    'Bayanan|Bayanan Public Market': (14.41095, 121.05163),
    'Bayanan|Bayanan Covered Court': (14.41095, 121.05163),
    'Bayanan|Bayanan Health Center': (14.41095, 121.05163),
    'Bayanan|Bayanan Elementary School': (14.40696, 121.04527),
    'Bayanan|Bayanan National High School': (14.41095, 121.05163),
    'Bayanan|National Road': (14.40957, 121.04628),
    'Bayanan|Bayanan Road': (14.41095, 121.05163),
    'Bayanan|San Guillermo Chapel': (14.40702, 121.05003),
    'Bayanan|Bayanan Multi-Purpose Hall': (14.41095, 121.05163),
    'Bayanan|Zone 1': (14.411, 121.051),
    'Bayanan|Zone 2': (14.409, 121.048),
    'Buli|Buli': (14.4433, 121.05058),
    'Buli|Buli Barangay Hall': (14.4433, 121.05058),
    'Buli|Buli Covered Court': (14.44335, 121.04804),
    'Buli|Buli Health Center': (14.4433, 121.05058),
    'Buli|Buli Elementary School': (14.44335, 121.04804),
    'Buli|Marina Road': (14.4435, 121.0495),
    'Buli|Buli Road': (14.4433, 121.05058),
    'Buli|East Service Road': (14.44555, 121.04548),
    'Buli|Lakefront Area': (14.4502, 121.051),
    'Buli|Buli Multi-Purpose Hall': (14.4433, 121.05058),
    'Buli|Buli Concepcion Road': (14.4433, 121.05058),
    'Buli|Zone 1': (14.444, 121.049),
    'Buli|Zone 2': (14.442, 121.051),
    'Cupang|Barangay Cupang Hall (NEW)': (14.43893, 121.05131),
    'Cupang|Old Barangay Cupang Hall': (14.43788, 121.05032),
    'Cupang|Barangay Cupang Multi-Purpose Hall': (14.43788, 121.05032),
    'Cupang|Cupang Covered Court': (14.43788, 121.05032),
    'Cupang|Cupang Elementary School': (14.4304, 121.04949),
    'Cupang|Cupang Health Center': (14.43788, 121.05032),
    'Cupang|Soldiers Hills Road': (14.40011, 121.03921),
    'Cupang|Daang Hari Road': (14.4234, 121.02256),
    'Cupang|East Service Road': (14.43577, 121.04557),
    'Cupang|Posadas Open Area': (14.4594, 121.04869),
    'Cupang|Zone 1': (14.438, 121.05),
    'Cupang|Zone 2': (14.435, 121.048),
    'Poblacion|Barangay Poblacion Hall': (14.38923, 121.04491),
    'Poblacion|Muntinlupa Central Market': (14.38992, 121.04808),
    'Poblacion|Southville 3 Covered Court': (14.37185, 121.0237),
    'Poblacion|KVHAI Covered Court': (14.38508, 121.02261),
    'Poblacion|Covered Court': (14.38508, 121.02261),
    'Poblacion|Muntinlupa City Hall': (14.39511, 121.04419),
    'Poblacion|Medical Center Muntinlupa': (14.39496, 121.0452),
    'Poblacion|National Road': (14.38836, 121.04541),
    'Poblacion|Rizal Street': (14.38992, 121.04808),
    'Poblacion|Katarungan Road': (14.38508, 121.02261),
    'Poblacion|Zone 1': (14.39, 121.046),
    'Poblacion|Zone 2': (14.387, 121.044),
    'Putatan|Barangay Putatan Barangay Hall': (14.395, 121.04832),
    'Putatan|Mutual Homes 1&2 Covered Court': (14.40099, 121.03288),
    'Putatan|SMB Hills Covered Court': (14.39497, 121.04483),
    'Putatan|Puregold Putatan': (14.39831, 121.04637),
    'Putatan|Savemore Putatan': (14.39681, 121.04418),
    'Putatan|Pedro Diaz Street': (14.39223, 121.04199),
    'Putatan|Country Homes Avenue': (14.39125, 121.04463),
    'Putatan|Putatan Health Center': (14.39497, 121.04483),
    'Putatan|Putatan Elementary School': (14.39433, 121.05009),
    'Putatan|National Road': (14.39722, 121.04511),
    'Putatan|Zone 1': (14.396, 121.046),
    'Putatan|Zone 2': (14.393, 121.043),
    'Sucat|Sucat': (14.45698, 121.05316),
    'Sucat|Sucat Barangay Hall': (14.4568, 121.05358),
    'Sucat|Sucat Multi-Purpose Covered Court': (14.45698, 121.05316),
    'Sucat|Bagong Silang Plaza Covered Court': (14.44482, 121.04257),
    'Sucat|Sitio Pagkakaisa Covered Court': (14.46608, 121.04709),
    'Sucat|Lakefront Open Area': (14.46632, 121.05422),
    'Sucat|Dr. A. Santos Avenue': (14.45361, 121.04565),
    'Sucat|East Service Road': (14.44977, 121.04547),
    'Sucat|West Service Road': (14.4479, 121.04516),
    'Sucat|Sucat Health Center': (14.45698, 121.05316),
    'Sucat|Sucat Elementary School': (14.45754, 121.05269),
    'Sucat|Zone 1': (14.457, 121.052),
    'Sucat|Zone 2': (14.454, 121.048),
    'Tunasan|Tunasan Barangay Hall': (14.37684, 121.04791),
    'Tunasan|Tunasan Covered Court': (14.38011, 121.05148),
    'Tunasan|Covered Court': (14.38011, 121.05148),
    "Tunasan|Tunasan People's Market": (14.37567, 121.04759),
    'Tunasan|Tunasan Health Center': (14.38011, 121.05148),
    'Tunasan|Tunasan Elementary School': (14.38105, 121.05149),
    'Tunasan|National Road': (14.38739, 121.04543),
    'Tunasan|Rodriguez Street': (14.37768, 121.04953),
    'Tunasan|Buendia Street': (14.37956, 121.0525),
    'Tunasan|Golden Gate Park Homes': (14.37502, 121.04505),
    'Tunasan|Zone 1': (14.38, 121.05),
    'Tunasan|Zone 2': (14.377, 121.048),
}


def _sanitize(raw: Optional[Coord]) -> Optional[Coord]:
    """Range-validate a coordinate and undo lat/lng swaps (mirrors the website)."""
    if not raw or len(raw) < 2:
        return None
    lat, lng = float(raw[0]), float(raw[1])
    if lat > 90 and lng <= 90:
        lat, lng = lng, lat
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def resolve_stop_coords(barangay: str, street: str = "") -> dict:
    """Resolve a stop to its coordinate and a confidence label.

    Returns ``{coord, is_exact, is_approximate, is_flagged, reason}`` where
    ``coord`` is ``(latitude, longitude)`` or ``None``.
    """
    b_norm = (barangay or "").strip()
    s_norm = (street or "").strip()

    b_lookup = b_norm
    if re.search(r"ayala\s*alabang", b_norm, re.IGNORECASE):
        b_lookup = "New Alabang Village"
    if re.match(r"^barangay\s+", b_lookup, re.IGNORECASE):
        b_lookup = re.sub(r"^barangay\s+", "", b_lookup, flags=re.IGNORECASE).strip()

    result = None

    # 1. Direct landmark / street match.
    if b_lookup and s_norm:
        coord = _sanitize(LANDMARK_COORDS.get(f"{b_lookup}|{s_norm}"))
        if coord is None:
            coord = _sanitize(LANDMARK_COORDS.get(f"{b_norm}|{s_norm}"))
        if coord is not None:
            result = {
                "coord": coord,
                "is_exact": True,
                "is_approximate": False,
                "is_flagged": False,
                "reason": None,
            }

    # 2. Case-insensitive / partial match, preserving registry order.
    if result is None and s_norm:
        s_lower = s_norm.lower()
        for key, raw in LANDMARK_COORDS.items():
            b_part, _, s_part = key.partition("|")
            b_part = b_part.lower()
            s_part = s_part.lower()
            b_matches = (
                not b_lookup
                or b_part == b_lookup.lower()
                or b_part == b_norm.lower()
            )
            if b_matches and (
                s_part == s_lower or s_lower in s_part or s_part in s_lower
            ):
                coord = _sanitize(raw)
                if coord is not None:
                    result = {
                        "coord": coord,
                        "is_exact": True,
                        "is_approximate": False,
                        "is_flagged": False,
                        "reason": None,
                    }
                    break

    # 3. Barangay centroid fallback (approximate).
    if result is None:
        coord = _sanitize(
            BARANGAY_COORDS.get(b_lookup) or BARANGAY_COORDS.get(b_norm)
        )
        if coord is not None:
            result = {
                "coord": coord,
                "is_exact": False,
                "is_approximate": True,
                "is_flagged": False,
                "reason": (
                    f'Exact address "{s_norm}" not mapped; using barangay center.'
                    if s_norm
                    else None
                ),
            }

    # 4. Unresolved.
    if result is None:
        return {
            "coord": None,
            "is_exact": False,
            "is_approximate": False,
            "is_flagged": True,
            "reason": (
                "Coordinates could not be found for "
                + (b_norm or "Unknown Barangay")
                + (f" - {s_norm}" if s_norm else "")
            ),
        }

    return result
