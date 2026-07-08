"""
amenities.py — Fetch nearby parking, public toilet, and street lighting data
from OpenStreetMap via the Overpass API.

OpenStreetMap is used instead of Google Maps because Google Maps scraping
violates their Terms of Service.  OSM data is freely licensed (ODbL),
has excellent UK coverage, and includes free/paid metadata.

Strategy:
  - Queries are made on-demand when a site appears in search results.
  - Results are cached per-site in sites_cache.json with a weekly TTL.
  - A background thread performs the queries so searches are never blocked.
  - Rate limit: 1 request/second (respectful of Overpass public instances).

Search radius: 1 mile (1 609 m) for parking/toilets; a much tighter radius
for street lighting, since a single streetlight's glare stops mattering for
dark adaptation well before that (see LAMP_* constants below).
"""

import logging
import math
import time

import requests

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "StargazingRecommender/1.0 (openstreetmap.org)"}
SEARCH_RADIUS_M = 1609          # 1 mile in metres
REQUEST_DELAY = 1.2             # seconds between requests (be polite)
AMENITY_MAX_AGE_HOURS = 168     # refresh weekly

# Street lighting ("local light pollution"), distinct from the regional
# light_pollution rating: a site can sit in a genuinely dark area yet have a
# single unshielded lamp right at the car park that wrecks dark adaptation.
LAMP_SEARCH_RADIUS_M = 200       # no point looking further out than this
LAMP_SOLO_RADIUS_M = 50          # a single lamp this close is glare, full stop
LAMP_CLUSTER_RADIUS_M = 150      # ...but several lamps out to here also count
LAMP_CLUSTER_MIN_COUNT = 3       # e.g. a lit car park/junction, not one stray lamp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Great-circle distance in metres between two WGS-84 points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin(math.radians(lat2 - lat1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2)
        * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return round(2 * R * math.asin(math.sqrt(a)))


def _overpass_query(lat: float, lng: float) -> list[dict]:
    """
    Single Overpass API call to fetch parking areas, public toilets, and
    street lighting (lamp posts + lit paths/roads) near the given point.
    Returns a list of OSM elements with full geometry (nodes get lat/lon
    directly; ways get a list of their constituent points), so distances
    can be computed precisely rather than from a way's rough centre.
    """
    query = f"""[out:json][timeout:25];
(
  node(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=parking];
  way(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=parking];
  node(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=toilets];
  way(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=toilets];
  node(around:{LAMP_SEARCH_RADIUS_M},{lat},{lng})[highway=street_lamp];
  way(around:{LAMP_SEARCH_RADIUS_M},{lat},{lng})[lit=yes];
);
out geom tags;
"""
    # Retry once on transient failures (timeouts, connection errors, and
    # retryable HTTP statuses) rather than silently recording an empty
    # result — indistinguishable from "genuinely nothing nearby" otherwise.
    # See weather.py's _request_forecast for the same pattern.
    for attempt in range(2):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (429, 502, 503, 504) and attempt < 1:
                logger.debug("Overpass HTTP %s for (%.4f, %.4f) — retrying.", status, lat, lng)
                time.sleep(2.0)
                continue
            logger.debug("Overpass query failed for (%.4f, %.4f): %s", lat, lng, exc)
            return []
        except requests.RequestException as exc:
            if attempt < 1:
                logger.debug("Overpass request error for (%.4f, %.4f) — retrying: %s", lat, lng, exc)
                time.sleep(2.0)
                continue
            logger.debug("Overpass query failed for (%.4f, %.4f): %s", lat, lng, exc)
            return []
    return []


def _nearest(
    elements: list[dict],
    amenity_type: str,
    site_lat: float,
    site_lng: float,
) -> dict | None:
    """
    From Overpass elements, return the closest publicly accessible amenity
    of the given type.  Private, customers-only amenities are excluded.
    """
    candidates: list[dict] = []

    for el in elements:
        tags = el.get("tags", {})
        if tags.get("amenity") != amenity_type:
            continue

        access = tags.get("access", "yes").lower()
        if access in ("private", "no", "customers", "permit"):
            continue

        if el["type"] == "node":
            elat, elng = el["lat"], el["lon"]
        elif el["type"] == "way" and el.get("geometry"):
            pts = [p for p in el["geometry"] if p]
            if not pts:
                continue
            elat = sum(p["lat"] for p in pts) / len(pts)
            elng = sum(p["lon"] for p in pts) / len(pts)
        else:
            continue

        raw_fee = tags.get("fee", "unknown").lower()
        fee = raw_fee if raw_fee in ("yes", "no") else "unknown"

        name = (
            tags.get("name")
            or tags.get("operator")
            or ("Car park" if amenity_type == "parking" else "Public toilet")
        )

        candidates.append({
            "name": name,
            "distance_m": _haversine_m(site_lat, site_lng, elat, elng),
            "lat": elat,
            "lng": elng,
            "fee": fee,          # "yes" = paid, "no" = free, "unknown"
            "access": access,
        })

    if not candidates:
        return None
    return min(candidates, key=lambda x: x["distance_m"])


def _nearest_light_source(
    elements: list[dict],
    site_lat: float,
    site_lng: float,
) -> tuple[int | None, int]:
    """
    From Overpass elements, find the nearest street light source (a
    highway=street_lamp node, or the nearest point along a lit=yes way) and
    count how many distinct light sources sit within LAMP_CLUSTER_RADIUS_M.

    Each OSM element (lamp node or lit way) counts once toward the cluster
    count, using its own nearest point — a single long lit road doesn't get
    over-counted just because it has many vertices.

    Returns (nearest_distance_m, cluster_count). nearest_distance_m is None
    if no light source was found within LAMP_SEARCH_RADIUS_M.
    """
    element_distances: list[float] = []

    for el in elements:
        tags = el.get("tags", {})
        if tags.get("highway") != "street_lamp" and tags.get("lit") != "yes":
            continue

        if el["type"] == "node":
            element_distances.append(_haversine_m(site_lat, site_lng, el["lat"], el["lon"]))
        elif el["type"] == "way" and el.get("geometry"):
            pts = [p for p in el["geometry"] if p]
            if not pts:
                continue
            nearest_on_way = min(
                _haversine_m(site_lat, site_lng, p["lat"], p["lon"]) for p in pts
            )
            element_distances.append(nearest_on_way)

    if not element_distances:
        return None, 0

    nearest = min(element_distances)
    cluster_count = sum(1 for d in element_distances if d <= LAMP_CLUSTER_RADIUS_M)
    return round(nearest), cluster_count


def _has_local_light_pollution(nearest_m: int | None, cluster_count: int) -> bool:
    """A single close lamp, or a cluster of several further-but-still-near ones."""
    if nearest_m is None:
        return False
    if nearest_m <= LAMP_SOLO_RADIUS_M:
        return True
    return cluster_count >= LAMP_CLUSTER_MIN_COUNT and nearest_m <= LAMP_CLUSTER_RADIUS_M


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_site_amenities(site_lat: float, site_lng: float) -> dict:
    """
    Query Overpass for the nearest parking area, public toilet, and street
    light source near this site.

    Returns a dict suitable for merging into a site record:
      nearest_parking:        dict | None
      nearest_toilets:        dict | None
      nearest_street_lamp_m:  int | None  (distance to nearest light source)
      local_light_pollution:  bool        (see _has_local_light_pollution)
      amenities_fetched_at:   float       (Unix timestamp)
    """
    elements = _overpass_query(site_lat, site_lng)
    time.sleep(REQUEST_DELAY)   # rate-limit: be polite to the public server

    nearest_lamp_m, lamp_cluster_count = _nearest_light_source(elements, site_lat, site_lng)

    return {
        "nearest_parking": _nearest(elements, "parking", site_lat, site_lng),
        "nearest_toilets": _nearest(elements, "toilets", site_lat, site_lng),
        "nearest_street_lamp_m": nearest_lamp_m,
        "local_light_pollution": _has_local_light_pollution(nearest_lamp_m, lamp_cluster_count),
        "amenities_fetched_at": time.time(),
    }


def needs_refresh(site: dict) -> bool:
    """Return True if the site's OSM amenity data is missing or older than 7 days."""
    fetched_at = site.get("amenities_fetched_at", 0.0)
    if not fetched_at:
        return True
    return (time.time() - fetched_at) > (AMENITY_MAX_AGE_HOURS * 3600)
