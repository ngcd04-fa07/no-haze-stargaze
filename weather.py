"""
weather.py — Cloud cover forecasts via the Open-Meteo API (free, no key required).

Open-Meteo supports multiple locations per request (comma-separated lat/lng).
We batch sites in groups of 50 to stay within URL length limits.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BATCH_SIZE = 50  # max locations per API call


def _fetch_batch(
    sites: list[dict],
    start_date: datetime,
    end_date: datetime,
) -> dict[str, list[tuple[str, int]]]:
    """
    Fetch hourly cloud_cover for a batch of sites from Open-Meteo.

    Returns mapping: slug -> [(iso_time, cloud_cover_pct), ...]
    """
    lats = ",".join(str(s["latitude"]) for s in sites)
    lons = ",".join(str(s["longitude"]) for s in sites)

    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": "cloud_cover",
        "timezone": "Europe/London",
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("Open-Meteo API error: %s", exc)
        return {}

    results: dict[str, list[tuple[str, int]]] = {}

    if isinstance(data, list):
        # Multiple-location response
        for i, site in enumerate(sites):
            try:
                hourly = data[i]["hourly"]
                results[site["slug"]] = list(
                    zip(hourly["time"], hourly["cloud_cover"])
                )
            except (IndexError, KeyError, TypeError):
                pass
    elif isinstance(data, dict) and "hourly" in data:
        # Single-location response
        site = sites[0]
        try:
            hourly = data["hourly"]
            results[site["slug"]] = list(
                zip(hourly["time"], hourly["cloud_cover"])
            )
        except (KeyError, TypeError):
            pass

    return results


def get_cloud_cover_forecast(
    sites: list[dict],
    start_date: datetime,
    end_date: datetime,
) -> dict[str, list[tuple[str, int]]]:
    """
    Return hourly cloud cover for all sites in batches.

    Returns: slug -> [(iso_time_str, cloud_cover_0_100), ...]
    """
    all_results: dict[str, list[tuple[str, int]]] = {}

    for i in range(0, len(sites), BATCH_SIZE):
        batch = sites[i : i + BATCH_SIZE]
        batch_results = _fetch_batch(batch, start_date, end_date)
        all_results.update(batch_results)

    return all_results


def average_cloud_cover_in_window(
    slug: str,
    hourly_data: dict[str, list[tuple[str, int]]],
    window_start: datetime,
    window_end: datetime,
) -> Optional[float]:
    """
    Return the average cloud cover % for a site within a time window.

    window_start / window_end are timezone-naive datetimes in local time
    (Europe/London), matching the Open-Meteo response times.
    """
    records = hourly_data.get(slug)
    if not records:
        return None

    values = []
    for time_str, cover in records:
        try:
            t = datetime.fromisoformat(time_str)
        except ValueError:
            continue
        if window_start <= t < window_end:
            if cover is not None:
                values.append(cover)

    if not values:
        return None
    return round(sum(values) / len(values), 1)


def best_window_cloud_cover(
    slug: str,
    hourly_data: dict[str, list[tuple[str, int]]],
    night_start_hour: int,
    night_end_hour: int,
    query_dates: list[datetime],
    lunar_by_date: dict[str, float] | None = None,
) -> dict:
    """
    Find the best night window across given dates, using a combined score of
    cloud cover (70 %) and lunar illumination (30 %) when lunar data is provided.

    Returns a dict with keys: avg_cover, best_date, hourly (list of dicts).
    """
    best: Optional[dict] = None
    best_combined: float = float("inf")

    for date in query_dates:
        window_start = date.replace(
            hour=night_start_hour, minute=0, second=0, microsecond=0
        )
        # Night window wraps midnight
        if night_end_hour <= night_start_hour:
            window_end = (date + timedelta(days=1)).replace(
                hour=night_end_hour, minute=0, second=0, microsecond=0
            )
        else:
            window_end = date.replace(
                hour=night_end_hour, minute=0, second=0, microsecond=0
            )

        avg = average_cloud_cover_in_window(slug, hourly_data, window_start, window_end)
        if avg is None:
            continue

        # Combined score: lower is better
        date_str = date.strftime("%Y-%m-%d")
        lunar_pct = (lunar_by_date or {}).get(date_str, 0.0)
        combined = avg * 0.7 + lunar_pct * 0.3

        if combined < best_combined:
            best_combined = combined
            # Collect hourly breakdown for this window
            records = hourly_data.get(slug, [])
            hourly_breakdown = []
            for time_str, cover in records:
                try:
                    t = datetime.fromisoformat(time_str)
                except ValueError:
                    continue
                if window_start <= t < window_end:
                    hourly_breakdown.append({
                        "time": t.strftime("%H:%M"),
                        "cloud_cover": cover,
                    })

            best = {
                "avg_cover": avg,
                "best_date": date_str,
                "window_start": window_start.strftime("%H:%M"),
                "window_end": window_end.strftime("%H:%M"),
                "hourly": hourly_breakdown,
            }

    return best or {"avg_cover": None, "best_date": None, "window_start": None,
                    "window_end": None, "hourly": []}
