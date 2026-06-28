"""
weather.py — Cloud cover forecasts via the Open-Meteo API (free, no key required).

Open-Meteo supports multiple locations per request (comma-separated lat/lng).
Requests are batched conservatively and cached per site/date range to avoid
hammering the API when users repeat similar searches.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BATCH_SIZE = 12
FORECAST_CACHE_TTL_SECONDS = 45 * 60
INTER_BATCH_DELAY_SECONDS = 0.65

_forecast_cache: dict[tuple[str, str, str], tuple[float, list[tuple[str, int]]]] = {}


class ForecastRateLimitError(Exception):
    pass


def _cache_key(site: dict, start_date: datetime, end_date: datetime) -> tuple[str, str, str]:
    return (
        site["slug"],
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )


def _get_cached_forecast(site: dict, start_date: datetime, end_date: datetime) -> Optional[list[tuple[str, int]]]:
    key = _cache_key(site, start_date, end_date)
    cached = _forecast_cache.get(key)
    if not cached:
        return None
    expires_at, records = cached
    if expires_at <= time.time():
        _forecast_cache.pop(key, None)
        return None
    return records


def _set_cached_forecast(
    site: dict,
    start_date: datetime,
    end_date: datetime,
    records: list[tuple[str, int]],
) -> None:
    key = _cache_key(site, start_date, end_date)
    _forecast_cache[key] = (time.time() + FORECAST_CACHE_TTL_SECONDS, records)


def _prune_forecast_cache() -> None:
    now = time.time()
    expired_keys = [key for key, (expires_at, _) in _forecast_cache.items() if expires_at <= now]
    for key in expired_keys:
        _forecast_cache.pop(key, None)


def _request_forecast(params: dict) -> dict | list | None:
    for attempt in range(4):
        try:
            resp = requests.get(OPEN_METEO_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                if attempt < 3:
                    time.sleep(2.5 * (attempt + 1))
                    continue
                raise ForecastRateLimitError() from exc
            logger.error("Open-Meteo API error: %s", exc)
            return None
        except requests.RequestException as exc:
            logger.error("Open-Meteo API error: %s", exc)
            return None
    return None


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
        data = _request_forecast(params)
    except ForecastRateLimitError:
        logger.warning(
            "Open-Meteo rate limit hit for batch of %d sites covering %s to %s.",
            len(sites),
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )
        return {}

    if data is None:
        return {}

    results: dict[str, list[tuple[str, int]]] = {}

    if isinstance(data, list):
        # Multiple-location response
        for i, site in enumerate(sites):
            try:
                hourly = data[i]["hourly"]
                records = list(
                    zip(hourly["time"], hourly["cloud_cover"])
                )
                results[site["slug"]] = records
                _set_cached_forecast(site, start_date, end_date, records)
            except (IndexError, KeyError, TypeError):
                pass
    elif isinstance(data, dict) and "hourly" in data:
        # Single-location response
        site = sites[0]
        try:
            hourly = data["hourly"]
            records = list(
                zip(hourly["time"], hourly["cloud_cover"])
            )
            results[site["slug"]] = records
            _set_cached_forecast(site, start_date, end_date, records)
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
    _prune_forecast_cache()

    all_results: dict[str, list[tuple[str, int]]] = {}
    uncached_sites: list[dict] = []

    for site in sites:
        cached = _get_cached_forecast(site, start_date, end_date)
        if cached is not None:
            all_results[site["slug"]] = cached
        else:
            uncached_sites.append(site)

    for i in range(0, len(uncached_sites), BATCH_SIZE):
        batch = uncached_sites[i : i + BATCH_SIZE]
        batch_results = _fetch_batch(batch, start_date, end_date)
        all_results.update(batch_results)
        if i + BATCH_SIZE < len(uncached_sites):
            time.sleep(INTER_BATCH_DELAY_SECONDS)

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
