#!/usr/bin/env python3
"""Fetch forecasts for all UK dark-sky sites and save to forecast_cache.json.

Run by GitHub Actions on a daily schedule.  Imports weather.py and scraper.py
from the repo root (parent of this file's directory).

Usage (from repo root):
    python scripts/fetch_forecast.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

# Repo root must be on sys.path so we can import weather and scraper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper  # noqa: E402
import weather  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("fetch_forecast")

FORECAST_LOOKAHEAD_DAYS = 14
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "forecast_cache.json")


def main() -> None:
    # ------------------------------------------------------------------
    # 1. Load sites from committed sites_cache.json
    # ------------------------------------------------------------------
    logger.info("Loading sites from cache…")
    sites, scraped_at = scraper.load_cache()
    if not sites:
        logger.error("No sites_cache.json found. Commit it to the repo first.")
        sys.exit(1)
    age_h = (time.time() - scraped_at) / 3600 if scraped_at else 0
    logger.info("Loaded %d sites (scraped %.1fh ago).", len(sites), age_h)

    # ------------------------------------------------------------------
    # 2. Run full forecast sweep
    # ------------------------------------------------------------------
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today + timedelta(days=FORECAST_LOOKAHEAD_DAYS)
    logger.info("Date range: %s → %s", today.date(), end_date.date())

    def on_checkpoint(partial: dict) -> None:
        logger.info("Checkpoint: %d/%d sites fetched.", len(partial), len(sites))

    forecast_data = weather.get_full_forecast_background(
        sites, today, end_date, on_batch_complete=on_checkpoint
    )

    if not forecast_data:
        logger.error("Forecast sweep returned no data — aborting.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. Save to forecast_cache.json (same format as app.py uses)
    # ------------------------------------------------------------------
    now = time.time()
    payload = {
        "cached_at": now,
        "data": {slug: list(records) for slug, records in forecast_data.items()},
        "site_timestamps": {slug: now for slug in forecast_data},
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(payload, f)

    size_mb = os.path.getsize(OUTPUT_FILE) / 1_000_000
    logger.info(
        "Done — saved %d/%d sites to %s (%.1f MB).",
        len(forecast_data), len(sites), OUTPUT_FILE, size_mb,
    )


if __name__ == "__main__":
    main()
