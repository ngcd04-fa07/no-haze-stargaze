"""
weather.py — Cloud cover forecasts via the Open-Meteo API (free, no key required).

Open-Meteo supports multiple locations per request (comma-separated lat/lng).
Requests are batched conservatively and cached per site/date range to avoid
hammering the API when users repeat similar searches.
"""

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None
import concurrent.futures
import json
import logging
import netrc  # pre-import: prevents import-lock contention when threads first call requests.get
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from config import DATA_DIR

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
BATCH_SIZE = 12
FORECAST_CACHE_TTL_SECONDS = 3 * 3600   # 3 h; GH Actions refreshes every 45 min so live sites never expire
INTER_BATCH_DELAY_SECONDS = 1.0         # short delay for on-demand inline fetches; background uses BACKGROUND_BATCH_DELAY_SECONDS
REQUEST_TIMEOUT_SECONDS = 30
RATE_LIMIT_COOLDOWN_SECONDS = 35 * 60  # default if no Retry-After header; 35 min is conservative for shared IPs
_RATE_LIMIT_STATE_FILE = str(DATA_DIR / "rate_limit_state.json")

_forecast_cache: dict[tuple[str, str, str], tuple[float, list[tuple[str, int]]]] = {}
_rate_limit_lock = threading.Lock()
_rate_limited_until = 0.0

# Thread pool for Open-Meteo HTTP calls; bounds DNS/pre-connect hangs that socket timeout cannot.
_weather_http_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="weather-http"
)


class ForecastRateLimitError(Exception):
    pass


def _mark_rate_limited(cooldown_seconds: float = RATE_LIMIT_COOLDOWN_SECONDS) -> None:
    global _rate_limited_until
    with _rate_limit_lock:
        _rate_limited_until = max(_rate_limited_until, time.time() + cooldown_seconds)
        # Persist so a process restart doesn't forget an active rate limit
        try:
            with open(_RATE_LIMIT_STATE_FILE, "w") as _f:
                json.dump({"rate_limited_until": _rate_limited_until}, _f)
        except Exception:
            pass


def _load_rate_limit_state() -> None:
    """Restore rate limit state saved by a previous process."""
    global _rate_limited_until
    try:
        with open(_RATE_LIMIT_STATE_FILE) as _f:
            saved = float(json.load(_f).get("rate_limited_until", 0))
        if saved > time.time():
            with _rate_limit_lock:
                _rate_limited_until = max(_rate_limited_until, saved)
            logger.info(
                "Restored rate limit state from disk: %.0f min remaining.",
                (saved - time.time()) / 60,
            )
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Could not load rate limit state: %s", exc)


_load_rate_limit_state()  # run once at module import


def _is_rate_limited() -> bool:
    with _rate_limit_lock:
        return time.time() < _rate_limited_until


def rate_limit_status() -> dict[str, float | bool]:
    with _rate_limit_lock:
        retry_after = max(0.0, _rate_limited_until - time.time())
    return {
        "active": retry_after > 0,
        "retry_after_seconds": round(retry_after, 1),
    }


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
    if _is_rate_limited():
        raise ForecastRateLimitError()

    for attempt in range(2):
        try:
            try:
                resp = _weather_http_executor.submit(
                    requests.get, OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS
                ).result(timeout=REQUEST_TIMEOUT_SECONDS + 2)
            except concurrent.futures.TimeoutError:
                logger.warning("Open-Meteo HTTP wall-clock deadline exceeded (DNS/pre-connect hang)")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                if attempt < 1:
                    time.sleep(1.0)
                    continue
                # Honour Retry-After if present; use longer of header value and default
                cooldown = RATE_LIMIT_COOLDOWN_SECONDS
                if exc.response is not None:
                    ra = exc.response.headers.get("Retry-After", "")
                    try:
                        cooldown = max(float(ra), RATE_LIMIT_COOLDOWN_SECONDS)
                    except (ValueError, TypeError):
                        pass
                _mark_rate_limited(cooldown)
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
        raise

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
        try:
            batch_results = _fetch_batch(batch, start_date, end_date)
        except ForecastRateLimitError:
            logger.warning(
                "Open-Meteo rate limit hit for batch of %d sites covering %s to %s; skipping remaining uncached forecasts for %.0f minutes.",
                len(batch),
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
                RATE_LIMIT_COOLDOWN_SECONDS / 60,
            )
            break
        all_results.update(batch_results)
        if i + BATCH_SIZE < len(uncached_sites):
            time.sleep(INTER_BATCH_DELAY_SECONDS)

    return all_results


def average_cloud_cover_in_window(
    slug: str,
    hourly_data: dict,
    window_start: datetime,
    window_end: datetime,
) -> Optional[float]:
    """Return the average cloud cover % for a site within a time window.

    hourly_data values are compact (t0_unix, bytes) tuples produced by
    app._compact_forecast(). window_start/window_end are naive datetimes
    (treated as UTC for arithmetic consistency with t0).
    """
    record = hourly_data.get(slug)
    if not record:
        return None
    t0, values = record
    ws = int(window_start.replace(tzinfo=timezone.utc).timestamp())
    we = int(window_end.replace(tzinfo=timezone.utc).timestamp())
    si = max(0, (ws - t0) // 3600)
    ei = min(len(values), (we - t0) // 3600)
    if si >= ei:
        return None
    window_vals = [v for v in values[si:ei] if v != 255]
    if not window_vals:
        return None
    return round(sum(window_vals) / len(window_vals), 1)


def best_window_cloud_cover(
    slug: str,
    hourly_data: dict,
    night_start_hour: int,
    night_end_hour: int,
    query_dates: list[datetime],
    lunar_by_date: dict[str, float] | None = None,
) -> dict:
    """Find the best night window across given dates.

    Uses combined score: cloud cover (70%) + lunar illumination (30%).
    hourly_data values are compact (t0_unix, bytes) tuples.
    Returns a dict with keys: avg_cover, best_date, window_start, window_end, hourly.
    """
    _empty = {"avg_cover": None, "best_date": None, "window_start": None,
              "window_end": None, "hourly": []}

    record = hourly_data.get(slug)
    if not record:
        return _empty
    t0, values = record

    best: Optional[dict] = None
    best_combined: float = float("inf")
    best_si = best_ei = 0

    for date in query_dates:
        window_start = date.replace(hour=night_start_hour, minute=0, second=0, microsecond=0)
        if night_end_hour <= night_start_hour:
            window_end = (date + timedelta(days=1)).replace(
                hour=night_end_hour, minute=0, second=0, microsecond=0
            )
        else:
            window_end = date.replace(hour=night_end_hour, minute=0, second=0, microsecond=0)

        ws = int(window_start.replace(tzinfo=timezone.utc).timestamp())
        we = int(window_end.replace(tzinfo=timezone.utc).timestamp())
        si = max(0, (ws - t0) // 3600)
        ei = min(len(values), (we - t0) // 3600)
        if si >= ei:
            continue

        window_vals = [v for v in values[si:ei] if v != 255]
        if not window_vals:
            continue
        avg = round(sum(window_vals) / len(window_vals), 1)

        date_str = date.strftime("%Y-%m-%d")
        lunar_pct = (lunar_by_date or {}).get(date_str, 0.0)
        combined = avg * 0.7 + lunar_pct * 0.3

        if combined < best_combined:
            best_combined = combined
            best_si, best_ei = si, ei
            best = {
                "avg_cover": avg,
                "best_date": date_str,
                "window_start": window_start.strftime("%H:%M"),
                "window_end": window_end.strftime("%H:%M"),
                "hourly": [],
            }

    if best is None:
        return _empty

    best["hourly"] = [
        {
            "time": datetime.fromtimestamp(t0 + i * 3600, tz=timezone.utc).strftime("%H:%M"),
            "cloud_cover": int(values[i]) if values[i] != 255 else None,
        }
        for i in range(best_si, best_ei)
    ]
    return best


# ---------------------------------------------------------------------------
# Background full-cache fetch (slow + conservative; never called from requests)
# ---------------------------------------------------------------------------

BACKGROUND_BATCH_SIZE = 20          # sites per Open-Meteo call
BACKGROUND_BATCH_DELAY_SECONDS = 15.0  # pause between batches; ~33 min for full sweep at this rate
BACKGROUND_CHECKPOINT_EVERY = 20    # call on_batch_complete every N successful batches


def get_full_forecast_background(
    sites: list[dict],
    start_date: datetime,
    end_date: datetime,
    on_batch_complete: "Optional[callable]" = None,
    abort_on_rate_limit: bool = False,
) -> dict[str, list[tuple[str, int]]]:
    """
    Fetch forecasts for ALL sites using conservative rate limits.
    Designed for background use only — no Gunicorn timeout pressure.

    abort_on_rate_limit=True: stop immediately on the first 429 and return
    partial results so far. The caller should merge with the existing cache.
    Use this from the Task Scheduler script to avoid multi-hour backoff waits
    that exceed the scheduler's ExecutionTimeLimit.

    abort_on_rate_limit=False (default): wait for the full cooldown then retry
    the same batch indefinitely (original behaviour; safe for manual runs).

    on_batch_complete(partial_results) is called every BACKGROUND_CHECKPOINT_EVERY
    successful batches so callers can persist incremental progress.
    """
    all_results: dict[str, list[tuple[str, int]]] = {}
    total_batches = -(-len(sites) // BACKGROUND_BATCH_SIZE)
    successful_batches = 0

    batch_positions = list(range(0, len(sites), BACKGROUND_BATCH_SIZE))

    if tqdm is not None:
        progress_iter = tqdm(
            enumerate(batch_positions),
            total=total_batches,
            desc="Fetching forecast batches",
            unit="batch",
            dynamic_ncols=True,
        )
    else:
        progress_iter = enumerate(batch_positions)

    for batch_idx, i in progress_iter:
        batch = sites[i: i + BACKGROUND_BATCH_SIZE]
        attempt = 0

        while True:  # retry this batch until it succeeds
            try:
                batch_results = _fetch_batch(batch, start_date, end_date)
                all_results.update(batch_results)
                successful_batches += 1

                if tqdm is None:
                    print(
                        f"Fetching forecast batches: "
                        f"{successful_batches}/{total_batches} complete "
                        f"({len(all_results)} sites cached)"
                    )
                else:
                    progress_iter.set_postfix({
                        "sites_cached": len(all_results),
                        "batch": f"{batch_idx + 1}/{total_batches}",
                    })

                break

            except ForecastRateLimitError:
                attempt += 1
                rl = rate_limit_status()

                if abort_on_rate_limit:
                    logger.warning(
                        "Background forecast: rate limit at batch %d/%d — aborting sweep with %d sites collected. "
                        "Retry-after: %.0f min. Existing cache will be merged.",
                        batch_idx + 1, total_batches, len(all_results), rl["retry_after_seconds"] / 60,
                    )
                    return all_results

                # Exponential backoff: each consecutive failure doubles the wait, capped at 4 hours.
                # This ensures we give the IP enough time to clear rather than re-triggering the ban.
                base_wait = rl["retry_after_seconds"] + 60
                wait_s = min(base_wait * (2 ** (attempt - 1)), 4 * 3600)

                logger.warning(
                    "Background forecast: rate limit at batch %d/%d (attempt %d) — waiting %.0f min then retrying.",
                    batch_idx + 1, total_batches, attempt, wait_s / 60,
                )

                if tqdm is not None:
                    progress_iter.set_postfix({
                        "rate_limited": "yes",
                        "wait_min": round(wait_s / 60, 1),
                        "batch": f"{batch_idx + 1}/{total_batches}",
                    })

                time.sleep(wait_s)
                # loop back and retry the same batch — never abort

        # Incremental checkpoint
        if on_batch_complete is not None and successful_batches % BACKGROUND_CHECKPOINT_EVERY == 0:
            on_batch_complete(dict(all_results))

        if i + BACKGROUND_BATCH_SIZE < len(sites):
            time.sleep(BACKGROUND_BATCH_DELAY_SECONDS)

    return all_results