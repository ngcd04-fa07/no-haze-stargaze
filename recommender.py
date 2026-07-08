"""
recommender.py — Score and rank stargazing sites.

Scoring (all components 0–100, weighted sum):

  Cloud cover   40%  (100 − avg_cloud_cover_pct)  ← most important
  Light pollution 35% — itself a blend of:
      Site light pollution rating  75%  (Dark site=100, Rural=80, Semi-rural=60, Suburban=40, Urban=20)
      Street lighting                25%  (graduated by distance to nearest OSM street lamp — see amenities.py)
  Distance       15%  (100 at origin, 0 at max_distance_km)
  Site type      10%  (Dark Sky Discovery=100, Recommended=85, Go Stargazing=70, Aurora=70)
"""

import math
from typing import Optional

import amenities

# Weights for the two components blended into pollution_score.
SITE_POLLUTION_WEIGHT = 0.75
STREET_LIGHT_WEIGHT = 0.25

LIGHT_POLLUTION_SCORE: dict[str, int] = {
    "dark site":  100,
    "rural":       80,
    "semi-rural":  60,
    "suburban":    40,
    "urban":       20,
    "unknown":     50,
}

SITE_TYPE_SCORE: dict[str, int] = {
    "Dark Sky Discovery Site":    100,
    "Recommended Stargazing Site": 85,
    "Go Stargazing Site":          70,
    "Aurora Viewpoint":            70,
    "Unknown":                     50,
}

# Human-readable labels for the frontend
LIGHT_POLLUTION_LABEL: dict[str, str] = {
    "dark site":  "Dark site",
    "rural":      "Rural",
    "semi-rural": "Semi-rural",
    "suburban":   "Suburban",
    "urban":      "Urban",
    "unknown":    "Unknown",
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _street_light_score(nearest_lamp_m: Optional[int]) -> float:
    """
    Score 0-100 for local street lighting: 100 = no nearby light source,
    0 = a lamp within LAMP_SOLO_RADIUS_M (glare, full stop). Linearly
    interpolated between LAMP_SOLO_RADIUS_M and LAMP_CLUSTER_RADIUS_M so
    scoring doesn't have an artificial cliff at either threshold.
    """
    if nearest_lamp_m is None:
        return 100.0
    if nearest_lamp_m <= amenities.LAMP_SOLO_RADIUS_M:
        return 0.0
    if nearest_lamp_m >= amenities.LAMP_CLUSTER_RADIUS_M:
        return 100.0
    span = amenities.LAMP_CLUSTER_RADIUS_M - amenities.LAMP_SOLO_RADIUS_M
    return round(100.0 * (nearest_lamp_m - amenities.LAMP_SOLO_RADIUS_M) / span, 1)


def score_site(
    site: dict,
    origin_lat: float,
    origin_lon: float,
    max_distance_km: float,
    avg_cloud_cover: Optional[float],
    weather_info: Optional[dict] = None,
    lunar_illumination_pct: Optional[float] = None,
    ignore_distance: bool = False,
) -> Optional[dict]:
    """
    Score a single site. Returns None if the site is outside the radius.

    avg_cloud_cover: average cloud cover % (0–100) during the target window.
    weather_info: dict with 'best_date', 'window_start', 'window_end', 'hourly'.
    lunar_illumination_pct: Moon illumination % for the best night (0=new moon, 100=full).
    ignore_distance: if True, distance no longer affects scoring.
    """
    dist_km = haversine_km(
        origin_lat, origin_lon, site["latitude"], site["longitude"]
    )
    if not ignore_distance and dist_km > max_distance_km:
        return None

    distance_score = 0.0
    if not ignore_distance:
        distance_score = max(0.0, 100.0 * (1.0 - dist_km / max_distance_km))

    pollution_level = site.get("light_pollution", "unknown")
    site_pollution_score = LIGHT_POLLUTION_SCORE.get(pollution_level, 50)
    street_light_score = _street_light_score(site.get("nearest_street_lamp_m"))
    pollution_score = (
        site_pollution_score * SITE_POLLUTION_WEIGHT
        + street_light_score * STREET_LIGHT_WEIGHT
    )

    site_type = site.get("site_type", "Unknown")
    type_score = SITE_TYPE_SCORE.get(site_type, 50)

    if avg_cloud_cover is not None:
        cloud_score = max(0.0, 100.0 - avg_cloud_cover)
    else:
        cloud_score = 50.0  # neutral when data unavailable

    if lunar_illumination_pct is not None:
        lunar_score = max(0.0, 100.0 - lunar_illumination_pct)
    else:
        lunar_score = 75.0  # assume reasonably dark when unknown

    if ignore_distance:
        total_score = (
            cloud_score * 0.38
            + pollution_score * 0.37
            # + type_score * 0.10
        ) / 0.75
    else:
        total_score = (
            cloud_score * 0.38
            + pollution_score * 0.37
            + distance_score * 0.25
            # + type_score * 0.10
        ) / 1.00

    return {
        "site": site,
        "distance_km": round(dist_km, 1),
        "total_score": round(min(total_score, 100.0), 1),
        "cloud_score": round(cloud_score, 1),
        "pollution_score": round(pollution_score, 1),
        "site_pollution_score": round(site_pollution_score, 1),
        "street_light_score": round(street_light_score, 1),
        "distance_score": round(distance_score, 1),
        "type_score": round(type_score, 1),
        "lunar_score": round(lunar_score, 1),
        "avg_cloud_cover": round(avg_cloud_cover, 1) if avg_cloud_cover is not None else None,
        "light_pollution_label": LIGHT_POLLUTION_LABEL.get(pollution_level, pollution_level),
        "weather": weather_info or {},
    }


def recommend(
    origin_lat: float,
    origin_lon: float,
    sites: list[dict],
    cloud_data: dict[str, float],   # slug -> avg cloud cover %
    weather_details: dict[str, dict],  # slug -> weather_info dict
    max_distance_km: float = 100.0,
    min_pollution_level: Optional[str] = None,
    require_parking: bool = False,
    require_toilets: bool = False,
    lunar_by_date: dict[str, float] | None = None,
    ignore_distance: bool = False,
    top_n: int = 25,
) -> tuple[list[dict], list[dict]]:
    """
    Return (recommendations, restricted_recommendations): two top-N lists
    ranked by score, filtered by radius, pollution level, parking requirement,
    and toilet requirement.

    Confirmed restricted_access sites are hard-filtered out of the main
    `recommendations` list and returned separately in
    `restricted_recommendations` instead — the frontend only merges them back
    in if the user opts in via the "Show Restricted Access sites" checkbox.
    likely_restricted_access sites are NOT filtered; they stay in the main
    list as before (chip + tooltip only).
    """
    min_pollution_score = 0
    if min_pollution_level:
        min_pollution_score = LIGHT_POLLUTION_SCORE.get(
            min_pollution_level.lower(), 0
        )

    scored: list[dict] = []

    for site in sites:
        # Filter by minimum light pollution quality
        site_pol_score = LIGHT_POLLUTION_SCORE.get(
            site.get("light_pollution", "unknown"), 50
        )
        if site_pol_score < min_pollution_score:
            continue

        # Parking filter: exclude sites where parking is False (confirmed absent)
        if require_parking and not site.get("has_parking", False):
            continue

        # Toilets filter: only include sites with confirmed toilets
        if require_toilets and site.get("has_toilets") is not True:
            continue

        slug = site["slug"]
        avg_cloud = cloud_data.get(slug)
        w_info = weather_details.get(slug)

        # Get lunar illumination for this site's best night
        best_date = (w_info or {}).get("best_date")
        lunar_pct = (lunar_by_date or {}).get(best_date) if best_date else None

        result = score_site(
            site, origin_lat, origin_lon, max_distance_km, avg_cloud, w_info,
            lunar_illumination_pct=lunar_pct,
            ignore_distance=ignore_distance,
        )
        if result:
            scored.append(result)

    scored.sort(key=lambda x: x["total_score"], reverse=True)

    open_sites = [r for r in scored if not r["site"].get("restricted_access")]
    restricted_sites = [r for r in scored if r["site"].get("restricted_access")]

    return open_sites[:top_n], restricted_sites[:top_n]
