#!/usr/bin/env python3
"""Refresh forecasts for the "live" set of sites (last searched within 48 hours).

Run by GitHub Actions on a 45-minute cron schedule.  Downloads the existing
forecast_cache.json from the GitHub Release to obtain last_requested_at
timestamps, filters down to the live set, fetches forecasts only for those
sites, merges results back into the full cache, and saves the output.

Usage (from repo root):
    python scripts/fetch_forecast.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests  # installed by the GitHub Actions step

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import weather  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("fetch_forecast")

FORECAST_LOOKAHEAD_DAYS = 14
LIVE_SET_WINDOW_SECONDS = 48 * 3600  # sites requested within this window are "live"
OUTPUT_FILE = REPO_ROOT / "forecast_cache.json"
FORECAST_REMOTE_URL = (
    "https://github.com/ngcd04-fa07/no-haze-stargaze/releases/download/"
    "forecast-latest/forecast_cache.json"
)


def _download_existing_cache() -> dict:
    """Download the current forecast_cache.json from the GitHub Release.

    Returns the parsed JSON payload, or an empty dict on any failure.
    """
    try:
        logger.info("Downloading existing cache from GitHub Release…")
        resp = requests.get(FORECAST_REMOTE_URL, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        age_h = (time.time() - float(payload.get("cached_at", 0))) / 3600
        logger.info(
            "Downloaded existing cache: %d sites, last saved %.1fh ago.",
            len(payload.get("data", {})), age_h,
        )
        return payload
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.info("No existing release found — starting fresh.")
        else:
            logger.warning("Could not download existing cache: %s", exc)
    except Exception as exc:
        logger.warning("Could not download existing cache: %s", exc)
    return {}


def main() -> None:
    # ------------------------------------------------------------------
    # 1. Download existing cache to obtain last_requested_at + cached data
    # ------------------------------------------------------------------
    existing_payload = _download_existing_cache()
    existing_data = {
        slug: [tuple(x) for x in records]
        for slug, records in existing_payload.get("data", {}).items()
    }
    existing_site_ts = {
        slug: float(t)
        for slug, t in existing_payload.get("site_timestamps", {}).items()
    }
    last_requested_at = {
        slug: float(t)
        for slug, t in existing_payload.get("last_requested_at", {}).items()
    }

    # ------------------------------------------------------------------
    # 2. Load all sites from sites_cache.json
    # ------------------------------------------------------------------
    sites_cache_path = REPO_ROOT / "sites_cache.json"
    logger.info("Loading sites from %s…", sites_cache_path)
    try:
        with open(sites_cache_path, encoding="utf-8") as f:
            sites_payload = json.load(f)
        all_sites = sites_payload.get("sites", [])
        scraped_at = float(sites_payload.get("scraped_at", 0))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Could not read sites_cache.json: %s", exc)
        sys.exit(1)
    if not all_sites:
        logger.error("sites_cache.json is empty — nothing to do.")
        sys.exit(1)
    age_h = (time.time() - scraped_at) / 3600 if scraped_at else 0
    logger.info("Loaded %d sites (scraped %.1fh ago).", len(all_sites), age_h)

    # ------------------------------------------------------------------
    # 3. Filter to the live set (sites searched within the last 48 hours)
    # ------------------------------------------------------------------
    cutoff = time.time() - LIVE_SET_WINDOW_SECONDS
    live_sites = [
        s for s in all_sites
        if last_requested_at.get(s["slug"], 0) >= cutoff
    ]
    logger.info(
        "Live set: %d/%d sites were searched in the last %.0fh.",
        len(live_sites), len(all_sites), LIVE_SET_WINDOW_SECONDS / 3600,
    )

    if not live_sites:
        logger.info("No live sites — nothing to fetch. Exiting without upload.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # 4. Fetch forecasts for the live set only
    # ------------------------------------------------------------------
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today + timedelta(days=FORECAST_LOOKAHEAD_DAYS)
    logger.info("Date range: %s → %s", today.date(), end_date.date())

    def on_checkpoint(partial: dict) -> None:
        logger.info(
            "Checkpoint: %d/%d live-set sites fetched so far.",
            len(partial), len(live_sites),
        )

    new_data = weather.get_full_forecast_background(
        live_sites, today, end_date, on_batch_complete=on_checkpoint
    )
    logger.info(
        "Fetched %d/%d live-set sites successfully.",
        len(new_data), len(live_sites),
    )

    if not new_data:
        logger.warning("No forecast data returned — aborting without upload.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 5. Merge with existing cache and save
    # ------------------------------------------------------------------
    now = time.time()
    merged_data = dict(existing_data)
    merged_data.update(new_data)
    merged_site_ts = dict(existing_site_ts)
    merged_site_ts.update({slug: now for slug in new_data})

    output_payload = {
        "cached_at": now,
        "data": {slug: list(records) for slug, records in merged_data.items()},
        "site_timestamps": merged_site_ts,
        "last_requested_at": last_requested_at,  # pass through unchanged
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output_payload, f)

    size_mb = os.path.getsize(OUTPUT_FILE) / 1_000_000
    logger.info(
        "Saved merged cache: %d total sites (%.1f MB). %d newly fetched from live set of %d.",
        len(merged_data), size_mb, len(new_data), len(live_sites),
    )


if __name__ == "__main__":
    main()
