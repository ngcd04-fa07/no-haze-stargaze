"""
geocoder.py — Convert a user-supplied location string to lat/lng.

Strategy:
  1. If it looks like a UK postcode → postcodes.io (fast, precise).
  2. Otherwise → Nominatim (OpenStreetMap), biased to Great Britain.
  3. If Nominatim fails/times out AND input is a postcode → retry postcodes.io.

Results are cached in-memory for the lifetime of the process (postcodes and
place names don't change, so no TTL is needed).

Each HTTP call runs in a thread with a hard wall-clock deadline so that DNS
stalls or pre-connect hangs cannot block the gunicorn worker, regardless of
whether the OS honours the socket-level timeout kwarg.
"""

import concurrent.futures
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_POSTCODE_RE = re.compile(
    r"^[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}$", re.IGNORECASE
)

NOMINATIM_HEADERS = {
    "User-Agent": "StargazingRecommender/1.0 (educational project)"
}

# In-memory cache: lowercased location string → (lat, lon, display_name)
_geocode_cache: dict[str, tuple[float, float, str]] = {}

# socket-level timeout covers read/write after the socket is open;
# _GEO_DEADLINE (wall-clock) also covers DNS and TCP-SYN which requests' timeout does not bound.
_GEO_TIMEOUT = 5.0
_GEO_DEADLINE = _GEO_TIMEOUT + 1.0

_geo_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="geo"
)


def _with_deadline(fn, *args) -> Optional[tuple[float, float, str]]:
    """Run fn(*args) in a background thread; return None if wall-clock exceeds _GEO_DEADLINE."""
    try:
        return _geo_executor.submit(fn, *args).result(timeout=_GEO_DEADLINE)
    except concurrent.futures.TimeoutError:
        logger.warning("Geocode wall-clock deadline exceeded (%s)", fn.__name__)
        return None


def _geocode_postcode(postcode: str) -> Optional[tuple[float, float, str]]:
    clean = postcode.replace(" ", "").upper()
    try:
        resp = requests.get(
            f"https://api.postcodes.io/postcodes/{clean}", timeout=_GEO_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == 200 and data.get("result"):
            r = data["result"]
            display = r.get("postcode", clean)
            return float(r["latitude"]), float(r["longitude"]), display
    except requests.RequestException as exc:
        logger.debug("postcodes.io error for %s: %s", postcode, exc)
    return None


def _geocode_nominatim(place: str) -> Optional[tuple[float, float, str]]:
    params = {
        "q": place,
        "format": "json",
        "limit": 1,
        "countrycodes": "gb,ie",
        "addressdetails": 0,
    }
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            headers=NOMINATIM_HEADERS,
            timeout=_GEO_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            r = results[0]
            return float(r["lat"]), float(r["lon"]), r.get("display_name", place)
    except requests.RequestException as exc:
        logger.debug("Nominatim error for '%s': %s", place, exc)
    return None


def geocode(location_string: str) -> Optional[tuple[float, float, str]]:
    """
    Return (latitude, longitude, display_name) or None if not found.

    Accepts UK postcodes or any place name in Great Britain / Ireland.
    """
    loc = location_string.strip()
    if not loc:
        return None

    cache_key = loc.lower()
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    is_postcode = bool(_POSTCODE_RE.match(loc))

    if is_postcode:
        result = _with_deadline(_geocode_postcode, loc)
        if result:
            _geocode_cache[cache_key] = result
            return result

    result = _with_deadline(_geocode_nominatim, loc)

    # Nominatim failed for a postcode-shaped input — try postcodes.io as a last resort
    if result is None and is_postcode:
        result = _with_deadline(_geocode_postcode, loc)

    if result is not None:
        _geocode_cache[cache_key] = result
    return result
