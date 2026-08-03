#!/usr/bin/env python3
"""Full photo sweep: find the nearest Wikimedia Commons geotagged photo for every cached site.

Single-threaded and paced at commons_photos.REQUEST_DELAY between sites to
stay respectful of the free, keyless Commons API.

Safety guarantees (matching scripts/refresh_amenities.py):
- Writes to a .tmp file first; validates; atomically replaces the real file
  only after validation passes.
- Preserves the site list's own `scraped_at` timestamp.
- Never overwrites a valid existing cache with an empty one.

Usage (from repo root):
    python scripts/refresh_site_photos.py
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import commons_photos as cp  # noqa: E402
import scraper  # noqa: E402

OUTPUT_FILE = REPO_ROOT / "sites_cache.json"
TMP_FILE = REPO_ROOT / "sites_cache.json.tmp"
NULL_RESULT_WARN_FRACTION = 0.7  # warn if >70% of sites found no photo at all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("refresh_site_photos")


def main() -> None:
    logger.info("=== Photo sweep started ===")
    sites, scraped_at = scraper.load_cache()
    if not sites:
        logger.error("sites_cache.json has no sites — nothing to do.")
        sys.exit(1)

    total = len(sites)
    logger.info(
        "Fetching nearest Commons photo for %d sites (~%.0f min at %.1fs/site)...",
        total, total * cp.REQUEST_DELAY / 60, cp.REQUEST_DELAY,
    )

    found = 0
    for i, site in enumerate(sites, start=1):
        try:
            photo = cp.fetch_nearest_photo(site["latitude"], site["longitude"])
        except Exception as exc:
            logger.debug("Photo fetch error for %s: %s", site["slug"], exc)
            photo = None
        if photo:
            site.update(photo)
            found += 1
        else:
            site["photo_thumb_url"] = None
            site["photo_fetched_at"] = time.time()
        time.sleep(cp.REQUEST_DELAY)
        if i % 200 == 0 or i == total:
            logger.info("Progress: %d/%d sites (%d found a photo so far)", i, total, found)

    null_fraction = 1 - (found / total)
    if null_fraction > NULL_RESULT_WARN_FRACTION:
        logger.warning(
            "%.0f%% of sites found no nearby photo at all — higher than expected, "
            "may indicate the Commons API had problems during this run.",
            null_fraction * 100,
        )

    payload = {"scraped_at": scraped_at, "sites": sites}
    TMP_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    written = json.loads(TMP_FILE.read_bytes())
    if not written.get("sites"):
        TMP_FILE.unlink(missing_ok=True)
        logger.error("Temp-file validation failed (empty sites list) — aborting.")
        sys.exit(1)
    os.replace(TMP_FILE, OUTPUT_FILE)

    logger.info(
        "=== Photo sweep complete: %d/%d sites got a photo ===",
        found, total,
    )


if __name__ == "__main__":
    main()
