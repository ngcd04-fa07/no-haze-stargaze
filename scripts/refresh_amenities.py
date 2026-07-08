#!/usr/bin/env python3
"""Full amenity sweep: query Overpass for parking/toilets/street lighting near every cached site.

Replaces the old per-request reactive amenity fetch (previously triggered
inline from live user searches in app.py) with a scheduled bulk sweep.
Single-threaded and paced at amenities.REQUEST_DELAY between requests
(currently 1.2s) to stay respectful of the free public Overpass instance —
~2625 sites takes roughly an hour. Intended to run monthly.

Safety guarantees (matching scripts/refresh_forecast_cache.py):
- Writes to a .tmp file first; validates; atomically replaces the real file
  only after validation passes.
- Preserves the site list's own `scraped_at` timestamp — this is a separate
  concern from the gostargazing.co.uk site-list scrape.
- Never overwrites a valid existing cache with an empty one.

Usage (from repo root):
    python scripts/refresh_amenities.py
"""

import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import amenities as am  # noqa: E402
import scraper  # noqa: E402

OUTPUT_FILE = REPO_ROOT / "sites_cache.json"
TMP_FILE = REPO_ROOT / "sites_cache.json.tmp"
NULL_RESULT_WARN_FRACTION = 0.5  # warn if >50% of sites found nothing nearby

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("refresh_amenities")


def main() -> None:
    logger.info("=== Amenity sweep started ===")
    sites, scraped_at = scraper.load_cache()
    if not sites:
        logger.error("sites_cache.json has no sites — nothing to do.")
        sys.exit(1)

    total = len(sites)
    logger.info(
        "Fetching amenities for %d sites (~%.0f min at %.1fs/site)...",
        total, total * am.REQUEST_DELAY / 60, am.REQUEST_DELAY,
    )

    both_null = 0
    for i, site in enumerate(sites, start=1):
        try:
            data = am.fetch_site_amenities(site["latitude"], site["longitude"])
        except Exception as exc:
            logger.debug("Amenity fetch error for %s: %s", site["slug"], exc)
            continue
        site.update(data)
        if data["nearest_parking"] is None and data["nearest_toilets"] is None:
            both_null += 1
        if i % 200 == 0 or i == total:
            logger.info("Progress: %d/%d sites (%d found nothing nearby so far)", i, total, both_null)

    null_fraction = both_null / total
    if null_fraction > NULL_RESULT_WARN_FRACTION:
        logger.warning(
            "%.0f%% of sites found no nearby parking/toilets at all — this is "
            "higher than expected and may indicate Overpass had problems during "
            "this run rather than genuine absence of amenities.",
            null_fraction * 100,
        )

    # Atomic write, preserving the site-list's own scraped_at.
    payload = {"scraped_at": scraped_at, "sites": sites}
    TMP_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    written = json.loads(TMP_FILE.read_bytes())
    if not written.get("sites"):
        TMP_FILE.unlink(missing_ok=True)
        logger.error("Temp-file validation failed (empty sites list) — aborting.")
        sys.exit(1)
    os.replace(TMP_FILE, OUTPUT_FILE)

    logger.info(
        "=== Amenity sweep complete: %d sites updated, %d found nothing nearby ===",
        total, both_null,
    )


if __name__ == "__main__":
    main()
