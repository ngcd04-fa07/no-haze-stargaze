"""cloud_grid.py — UK cloud-cover grid for the map overlay.

Fetches hourly cloud_cover from Open-Meteo for a fixed 1° UK grid (110 pts).
Results are cached in memory and refreshed every hour.

Data structure is forward-compatible: each grid point carries a `layers` dict
so cloud_cover_low / mid / high can be added later without breaking callers.
"""

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_URL        = "https://api.open-meteo.com/v1/forecast"
CACHE_MAX_AGE_SECONDS = 3600   # 1 hour
_BATCH_SIZE           = 50     # matches weather.py's URL-safe batch size

# ── UK grid: 1° spacing ───────────────────────────────────────────────────────
_LATS = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0, 58.0, 59.0, 60.0]
_LNGS = [-7.0, -6.0, -5.0, -4.0, -3.0, -2.0, -1.0,  0.0,  1.0,  2.0]

GRID_POINTS: list[tuple[float, float]] = [
    (lat, lng) for lat in _LATS for lng in _LNGS
]  # 110 points total

# Layer variables — extend this dict when adding low/mid/high support
_LAYER_VARS: dict[str, str] = {
    "total": "cloud_cover",
    # "low":  "cloud_cover_low",   # uncomment to enable
    # "mid":  "cloud_cover_mid",
    # "high": "cloud_cover_high",
}

# ── Thread-safe in-memory cache ───────────────────────────────────────────────
_lock:  threading.Lock = threading.Lock()
_cache: dict | None    = None   # {"data": {...}, "fetched_at": float}


def _needs_refresh() -> bool:
    if _cache is None:
        return True
    return (time.time() - _cache["fetched_at"]) > CACHE_MAX_AGE_SECONDS


def _fetch_batch(pts: list[tuple[float, float]]) -> list[dict] | None:
    """Fetch cloud layer data for a batch of grid points. Returns None on error."""
    lats = ",".join(str(p[0]) for p in pts)
    lngs = ",".join(str(p[1]) for p in pts)
    params = {
        "latitude":     lats,
        "longitude":    lngs,
        "hourly":       ",".join(_LAYER_VARS.values()),
        "timezone":     "Europe/London",
        "forecast_days": 2,
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as exc:
        logger.warning("cloud_grid batch fetch failed: %s", exc)
        return None

    # Single-location responses come back as a dict; normalise to list
    if isinstance(raw, dict):
        raw = [raw]

    if len(raw) != len(pts):
        logger.warning(
            "cloud_grid: expected %d results, got %d", len(pts), len(raw)
        )
        return None

    return raw


def fetch_grid(force: bool = False) -> dict | None:
    """Return cloud-grid data (uses cache when fresh).

    Returns None only when no data is available at all (first fetch failure).
    On subsequent failures the stale cache is returned so the map keeps working.
    """
    global _cache

    with _lock:
        if not force and not _needs_refresh():
            return _cache["data"]

    # Fetch in batches, politely
    all_raw: list[dict] = []
    for i in range(0, len(GRID_POINTS), _BATCH_SIZE):
        chunk = GRID_POINTS[i: i + _BATCH_SIZE]
        raw   = _fetch_batch(chunk)
        if raw is None:
            # Return stale cache if we have one; otherwise nothing
            with _lock:
                return _cache["data"] if _cache else None
        all_raw.extend(raw)
        if i + _BATCH_SIZE < len(GRID_POINTS):
            time.sleep(0.4)   # polite pause between batches

    hours: list[str] = all_raw[0].get("hourly", {}).get("time", [])

    points_out: list[dict] = []
    for i, (lat, lng) in enumerate(GRID_POINTS):
        hourly = all_raw[i].get("hourly", {})
        layers: dict[str, list] = {
            key: hourly.get(var, [])
            for key, var in _LAYER_VARS.items()
        }
        points_out.append({"lat": lat, "lng": lng, "layers": layers})

    data: dict = {
        "hours":             hours,
        "points":            points_out,
        "available_layers":  list(_LAYER_VARS.keys()),
        "generated_at":      int(time.time()),
    }

    with _lock:
        _cache = {"data": data, "fetched_at": time.time()}

    logger.info(
        "cloud_grid: cached %d points × %d hours (layers: %s)",
        len(points_out), len(hours), list(_LAYER_VARS.keys()),
    )
    return data
