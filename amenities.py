"""
amenities.py — Fetch nearby parking and public toilet data from OpenStreetMap
via the Overpass API.

OpenStreetMap is used instead of Google Maps because Google Maps scraping
violates their Terms of Service.  OSM data is freely licensed (ODbL),
has excellent UK coverage, and includes free/paid metadata.

Strategy:
  - Queries are made on-demand when a site appears in search results.
  - Results are cached per-site in sites_cache.json with a weekly TTL.
  - A background thread performs the queries so searches are never blocked.
  - Rate limit: 1 request/second (respectful of Overpass public instances).

Search radius: 1 mile (1 609 m) around each site.
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
    Single Overpass API call to fetch parking areas AND public toilets
    within SEARCH_RADIUS_M of the given point.
    Returns a list of OSM elements (nodes and ways with centre coords).
    """
    query = f"""[out:json][timeout:25];
(
  node(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=parking];
  way(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=parking];
  node(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=toilets];
  way(around:{SEARCH_RADIUS_M},{lat},{lng})[amenity=toilets];
);
out center tags;
"""
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("elements", [])
    except requests.RequestException as exc:
        logger.debug("Overpass query failed for (%.4f, %.4f): %s", lat, lng, exc)
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
        elif el["type"] == "way" and "center" in el:
            elat = el["center"]["lat"]
            elng = el["center"]["lon"]
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_site_amenities(site_lat: float, site_lng: float) -> dict:
    """
    Query Overpass for the nearest parking area and public toilet within 1 mile.

    Returns a dict suitable for merging into a site record:
      nearest_parking:  dict | None
      nearest_toilets:  dict | None
      amenities_fetched_at: float  (Unix timestamp)
    """
    elements = _overpass_query(site_lat, site_lng)
    time.sleep(REQUEST_DELAY)   # rate-limit: be polite to the public server

    return {
        "nearest_parking": _nearest(elements, "parking", site_lat, site_lng),
        "nearest_toilets": _nearest(elements, "toilets", site_lat, site_lng),
        "amenities_fetched_at": time.time(),
    }


def needs_refresh(site: dict) -> bool:
    """Return True if the site's OSM amenity data is missing or older than 7 days."""
    fetched_at = site.get("amenities_fetched_at", 0.0)
    if not fetched_at:
        return True
    return (time.time() - fetched_at) > (AMENITY_MAX_AGE_HOURS * 3600)
