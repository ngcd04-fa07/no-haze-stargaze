"""
app.py — Flask web application for the Stargazing Recommender.

Run:
    python app.py

The app starts scraping site data from gostargazing.co.uk in the background
on first launch.  Results are cached in sites_cache.json.

Endpoints:
  GET  /                  — serve the single-page frontend
  GET  /api/status        — scraping progress + sites count
  POST /api/recommend     — generate recommendations
  POST /api/refresh       — trigger a fresh scrape (invalidates cache)
"""

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, render_template, request

import geocoder as geo
import amenities as am
import lunar as lu
import recommender as rec
import scraper
import weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

WEATHER_CANDIDATE_LIMIT = 75


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response

# ---------------------------------------------------------------------------
# Shared state (protected by a threading.Lock where mutated)
# ---------------------------------------------------------------------------
_state = {
    "sites": [],
    "scraping": False,
    "scrape_progress": {"done": 0, "total": 0, "found": 0},
    "scrape_started_at": 0.0,
    "scraped_at": 0.0,
    "next_refresh_at": 0.0,
    "lock": threading.Lock(),
}


def _progress_cb(done: int, total: int, found: int) -> None:
    with _state["lock"]:
        _state["scrape_progress"] = {
            "done": done,
            "total": total,
            "found": found,
        }


_INCREMENTAL_SAVE_EVERY = 100  # update shared state every N sites found


def _pick_weather_candidates(
    sites: list[dict],
    origin_lat: float,
    origin_lon: float,
    max_distance_km: float,
    ignore_distance: bool,
    limit: int = WEATHER_CANDIDATE_LIMIT,
) -> list[dict]:
    if len(sites) <= limit:
        return sites

    scored: list[tuple[float, dict]] = []
    for site in sites:
        result = rec.score_site(
            site,
            origin_lat,
            origin_lon,
            max_distance_km,
            avg_cloud_cover=None,
            weather_info=None,
            lunar_illumination_pct=None,
            ignore_distance=ignore_distance,
        )
        if result is not None:
            scored.append((result["total_score"], site))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [site for _, site in scored[:limit]]


def _site_found_cb(site: dict, all_sites: list[dict]) -> None:
    """Called each time a new site with coordinates is discovered."""
    if len(all_sites) % _INCREMENTAL_SAVE_EVERY == 0:
        with _state["lock"]:
            _state["sites"] = list(all_sites)
        # Also persist to cache so a restart can resume quickly
        try:
            scraper.save_cache(all_sites)
        except Exception:
            pass
        logger.info("Incremental update: %d sites available.", len(all_sites))


def _do_scrape(force: bool = False) -> None:
    """Background thread: scrape all sites and update shared state."""
    with _state["lock"]:
        if _state["scraping"]:
            return
        _state["scraping"] = True
        _state["scrape_started_at"] = time.time()

    try:
        # Try loading from cache first
        cached_sites, scraped_at = scraper.load_cache()
        if not force and cached_sites:
            # Use cache if it's fresh enough
            if scraper.is_cache_fresh(scraped_at):
                logger.info("Loaded %d sites from fresh cache.", len(cached_sites))
                with _state["lock"]:
                    _state["sites"] = cached_sites
                    _state["scraped_at"] = scraped_at
                return
            # Stale cache: pre-load so searches work while re-scraping
            logger.info(
                "Cache stale (%d sites) — loading it while re-scraping.",
                len(cached_sites),
            )
            with _state["lock"]:
                _state["sites"] = cached_sites
                _state["scraped_at"] = scraped_at

        # Fresh scrape
        logger.info("Starting full scrape from gostargazing.co.uk…")
        slugs = scraper.get_all_location_slugs()
        with _state["lock"]:
            _state["scrape_progress"] = {"done": 0, "total": len(slugs), "found": 0}

        sites = scraper.scrape_all(
            slugs,
            max_workers=4,
            progress_cb=_progress_cb,
            site_found_cb=_site_found_cb,
        )
        if sites:
            scraper.save_cache(sites)
            now = time.time()
            with _state["lock"]:
                _state["sites"] = sites
                _state["scraped_at"] = now
                _state["next_refresh_at"] = now + scraper.CACHE_MAX_AGE_SECONDS
            logger.info("Scrape complete: %d sites with coordinates.", len(sites))
        else:
            logger.warning("Scrape produced no results.")
    except Exception as exc:
        logger.exception("Scrape failed: %s", exc)
    finally:
        with _state["lock"]:
            _state["scraping"] = False
            _state["scrape_started_at"] = 0.0


def _start_background_scrape(force: bool = False) -> None:
    t = threading.Thread(target=_do_scrape, args=(force,), daemon=True)
    t.start()


_WEEKLY_CHECK_INTERVAL = 3600  # how often (seconds) the scheduler wakes to check


def _preload_cached_sites_on_startup() -> bool:
    """Load cached sites into shared state without triggering a scrape."""
    cached_sites, scraped_at = scraper.load_cache()
    if not cached_sites:
        return False

    with _state["lock"]:
        _state["sites"] = cached_sites
        _state["scraped_at"] = scraped_at
        if scraped_at > 0:
            _state["next_refresh_at"] = scraped_at + scraper.CACHE_MAX_AGE_SECONDS

    cache_age_hours = (time.time() - scraped_at) / 3600 if scraped_at > 0 else None
    if cache_age_hours is not None:
        logger.info(
            "Preloaded %d cached sites on startup (cache age %.1fh).",
            len(cached_sites),
            cache_age_hours,
        )
    else:
        logger.info("Preloaded %d cached sites on startup.", len(cached_sites))
    return True


def _weekly_cache_manager() -> None:
    """Daemon thread: keep refresh metadata current without auto-scraping stale cache."""
    while True:
        time.sleep(_WEEKLY_CHECK_INTERVAL)
        with _state["lock"]:
            scraping = _state["scraping"]
            scraped_at = _state["scraped_at"]
            sites_loaded = len(_state["sites"])
        if scraping or sites_loaded == 0 or scraped_at <= 0:
            continue
        if not scraper.is_cache_fresh(scraped_at):
            logger.info("Weekly cache manager: cache is stale; waiting for manual refresh.")


def _normalize_angle(angle: float) -> float:
    return angle % 360.0


def _sun_event_utc(date: datetime, latitude: float, longitude: float, is_sunrise: bool) -> datetime | None:
    n = date.timetuple().tm_yday
    lng_hour = longitude / 15.0
    t = n + ((6 - lng_hour) / 24.0) if is_sunrise else n + ((18 - lng_hour) / 24.0)

    m = _normalize_angle(0.9856 * t - 3.289)
    l = _normalize_angle(m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634)

    ra = _normalize_angle(math.degrees(math.atan(0.91764 * math.tan(math.radians(l)))))
    l_quadrant = math.floor(l / 90.0) * 90.0
    ra_quadrant = math.floor(ra / 90.0) * 90.0
    ra = ra + (l_quadrant - ra_quadrant)
    ra = ra / 15.0

    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))

    cos_h = (
        math.cos(math.radians(90.833))
        - sin_dec * math.sin(math.radians(latitude))
    ) / (cos_dec * math.cos(math.radians(latitude)))
    if cos_h > 1 or cos_h < -1:
        return None

    h = math.degrees(math.acos(cos_h))
    if is_sunrise:
        h = 360.0 - h
    h = h / 15.0

    t_local = h + ra - (0.06571 * t) - 6.622
    ut = _normalize_angle(t_local - lng_hour)
    hours = int(math.floor(ut))
    minutes = int(round((ut - hours) * 60.0))
    if minutes >= 60:
        hours += 1
        minutes -= 60
    hours %= 24
    return datetime(date.year, date.month, date.day, hours, minutes, tzinfo=timezone.utc)


def _fallback_sunrise_sunset(date: datetime, latitude: float, longitude: float) -> tuple[str, str] | None:
    sunrise_utc = _sun_event_utc(date, latitude, longitude, True)
    sunset_utc = _sun_event_utc(date, latitude, longitude, False)
    if not sunrise_utc or not sunset_utc:
        return None
    uk_tz = ZoneInfo("Europe/London")
    sunrise_local = sunrise_utc.astimezone(uk_tz)
    sunset_local = sunset_utc.astimezone(uk_tz)
    return sunrise_local.strftime("%H:%M"), sunset_local.strftime("%H:%M")


# Preload cached data on app load; only scrape immediately if no cache exists.
if not _preload_cached_sites_on_startup():
    _start_background_scrape()

# Start the background weekly-refresh scheduler
threading.Thread(target=_weekly_cache_manager, daemon=True, name="weekly-cache").start()


# ---------------------------------------------------------------------------
# Amenity background fetcher
# ---------------------------------------------------------------------------

_amenity_in_flight: set[str] = set()   # slugs currently being fetched
_amenity_lock = threading.Lock()


def _fetch_amenities_bg(sites: list[dict]) -> None:
    """Background thread: query Overpass for each site and update the cache."""
    for site in sites:
        slug = site["slug"]
        try:
            data = am.fetch_site_amenities(site["latitude"], site["longitude"])
            with _state["lock"]:
                for s in _state["sites"]:
                    if s["slug"] == slug:
                        s.update(data)
                        break
            logger.debug("Amenities updated: %s", slug)
        except Exception as exc:
            logger.debug("Amenity fetch error (%s): %s", slug, exc)
        finally:
            with _amenity_lock:
                _amenity_in_flight.discard(slug)

    # Persist with the ORIGINAL scraped_at so weekly re-scrape isn't triggered
    with _state["lock"]:
        sites_copy = list(_state["sites"])
        original_ts = _state["scraped_at"]
    try:
        scraper.save_cache(sites_copy, scraped_at=original_ts)
        logger.info("Amenity data persisted to cache.")
    except Exception as exc:
        logger.warning("Could not save amenity cache: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    with _state["lock"]:
        return jsonify(
            {
                "sites_loaded": len(_state["sites"]),
                "scraping": _state["scraping"],
                "progress": _state["scrape_progress"],
                "scrape_started_at": _state["scrape_started_at"],
                "scraped_at": _state["scraped_at"],
                "next_refresh_at": _state["next_refresh_at"],
            }
        )


@app.route("/api/sunrise_sunset", methods=["POST"])
def api_sunrise_sunset():
    payload = request.get_json(silent=True) or {}
    location = (payload.get("location") or "").strip()
    all_uk = bool(payload.get("all_uk", False))
    date_str = (payload.get("date") or "").strip()

    if not date_str:
        return jsonify({"error": "date is required"}), 400

    try:
        base_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format — use YYYY-MM-DD"}), 400

    if all_uk or not location or location.lower() == "united kingdom":
        origin_lat, origin_lon, origin_name = 54.0, -2.0, "United Kingdom"
    else:
        origin = geo.geocode(location)
        if not origin:
            return jsonify({"error": f"Could not geocode location: {location!r}"}), 400
        origin_lat, origin_lon, origin_name = origin
    fallback = _fallback_sunrise_sunset(base_date, origin_lat, origin_lon)
    if fallback:
        sunrise, sunset = fallback
        return jsonify({"sunrise": sunrise, "sunset": sunset})
    return jsonify({"error": "Sunrise/sunset not available for that date"}), 502


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    with _state["lock"]:
        if _state["scraping"]:
            return jsonify({"error": "A scrape is already in progress."}), 409
    _start_background_scrape(force=True)
    return jsonify({"message": "Refresh started."})


@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    data = request.get_json(silent=True) or {}
    all_uk = bool(data.get("all_uk", False))

    # ---- Required parameters ----
    location_str = (data.get("location") or "").strip()
    if all_uk:
        location_str = "United Kingdom"
    if not location_str:
        return jsonify({"error": "location is required"}), 400

    # ---- Optional parameters with defaults ----
    try:
        max_distance_km = float(data.get("max_distance_km", 100))
        max_distance_km = max(10, min(max_distance_km, 2000))  # 2000 km covers all of UK
    except (TypeError, ValueError):
        max_distance_km = 100.0
    if all_uk:
        max_distance_km = 2000.0

    try:
        night_start_hour = int(data.get("night_start_hour", 22))
        night_start_hour = max(0, min(night_start_hour, 23))
    except (TypeError, ValueError):
        night_start_hour = 22

    try:
        night_end_hour = int(data.get("night_end_hour", 4))
        night_end_hour = max(0, min(night_end_hour, 23))
    except (TypeError, ValueError):
        night_end_hour = 4

    min_pollution_level = (data.get("min_pollution_level") or "").strip().lower() or None
    require_parking = bool(data.get("require_parking", False))
    require_toilets = bool(data.get("require_toilets", False))

    # Date range: default = tonight + next 3 days
    date_str = (data.get("date") or "").strip()
    if date_str:
        try:
            base_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid date format — use YYYY-MM-DD"}), 400
    else:
        base_date = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    num_nights = int(data.get("num_nights", 1))
    num_nights = max(1, min(num_nights, 7))

    query_dates = [base_date + timedelta(days=i) for i in range(num_nights)]

    # ---- Geocode origin ----
    origin = geo.geocode(location_str)
    if not origin:
        return jsonify({"error": f"Could not geocode location: {location_str!r}"}), 400
    origin_lat, origin_lon, origin_name = origin

    # ---- Filter sites by distance ----
    with _state["lock"]:
        all_sites = list(_state["sites"])

    if not all_sites:
        return jsonify(
            {"error": "Site data not yet loaded — please wait a moment and try again."}
        ), 503

    nearby = []
    for site in all_sites:
        dist = rec.haversine_km(origin_lat, origin_lon, site["latitude"], site["longitude"])
        if all_uk or dist <= max_distance_km:
            # Apply pollution filter early to reduce weather API calls
            if min_pollution_level:
                pol_score = rec.LIGHT_POLLUTION_SCORE.get(
                    site.get("light_pollution", "unknown"), 50
                )
                min_score = rec.LIGHT_POLLUTION_SCORE.get(min_pollution_level, 0)
                if pol_score < min_score:
                    continue
            if require_parking and not site.get("has_parking", False):
                continue
            if require_toilets and site.get("has_toilets") is not True:
                continue
            nearby.append(site)

    if not nearby:
        return jsonify(
            {
                "recommendations": [],
                "origin": {
                    "lat": origin_lat,
                    "lon": origin_lon,
                    "name": origin_name,
                },
                "sites_checked": 0,
                "message": (
                    f"No sites found within {max_distance_km:.0f} km "
                    f"matching the specified criteria."
                ),
            }
        )

    # ---- Compute lunar illumination for each query date ----
    lunar_by_date: dict[str, float] = {
        d.strftime("%Y-%m-%d"): lu.lunar_illumination(d) for d in query_dates
    }

    weather_candidates = _pick_weather_candidates(
        nearby,
        origin_lat,
        origin_lon,
        max_distance_km,
        ignore_distance=all_uk,
    )
    logger.info(
        "Weather fetch limited to %d of %d candidate sites.",
        len(weather_candidates),
        len(nearby),
    )

    # ---- Fetch cloud cover for top candidate sites only ----
    weather_start = query_dates[0]
    weather_end = query_dates[-1] + timedelta(days=1)
    hourly_data = weather.get_cloud_cover_forecast(weather_candidates, weather_start, weather_end)

    # ---- Build per-site cloud summary ----
    cloud_data: dict[str, float] = {}
    weather_details: dict[str, dict] = {}

    for site in weather_candidates:
        slug = site["slug"]
        w_info = weather.best_window_cloud_cover(
            slug, hourly_data, night_start_hour, night_end_hour, query_dates,
            lunar_by_date=lunar_by_date,
        )
        weather_details[slug] = w_info
        if w_info["avg_cover"] is not None:
            cloud_data[slug] = w_info["avg_cover"]

    # ---- Generate recommendations ----
    recommendations = rec.recommend(
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        sites=weather_candidates,
        cloud_data=cloud_data,
        weather_details=weather_details,
        max_distance_km=max_distance_km,
        min_pollution_level=min_pollution_level,
        require_parking=require_parking,
        require_toilets=require_toilets,
        lunar_by_date=lunar_by_date,
        ignore_distance=all_uk,
        top_n=25,
    )

    # ---- Kick off background amenity fetch for sites that need it ----
    with _amenity_lock:
        to_fetch = [
            r["site"] for r in recommendations
            if am.needs_refresh(r["site"]) and r["site"]["slug"] not in _amenity_in_flight
        ]
        for site in to_fetch:
            _amenity_in_flight.add(site["slug"])
    if to_fetch:
        threading.Thread(
            target=_fetch_amenities_bg, args=(to_fetch,), daemon=True,
            name="amenity-fetch",
        ).start()
        logger.info("Queued amenity fetch for %d sites.", len(to_fetch))

    # Lunar summary for the primary search date
    primary_date = base_date
    primary_lunar = lu.lunar_info(primary_date)

    return jsonify(
        {
            "recommendations": recommendations,
            "origin": {"lat": origin_lat, "lon": origin_lon, "name": origin_name},
            "lunar": primary_lunar,
            "lunar_by_date": lunar_by_date,
            "sites_checked": len(nearby),
            "weather_sites_checked": len(weather_candidates),
            "all_uk": all_uk,
            "query": {
                "date": base_date.strftime("%Y-%m-%d"),
                "num_nights": num_nights,
                "night_start_hour": night_start_hour,
                "night_end_hour": night_end_hour,
                "max_distance_km": max_distance_km,
                "min_pollution_level": min_pollution_level,
                "require_parking": require_parking,
                "require_toilets": require_toilets,
                "all_uk": all_uk,
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5003"))
    app.run(debug=False, port=port, host="0.0.0.0")
