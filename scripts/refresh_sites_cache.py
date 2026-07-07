#!/usr/bin/env python3
"""Refresh sites_cache.json from gostargazing.co.uk.

Two modes:
  --mode full        Full re-scrape: rediscovers all location slugs from the
                      sitemaps and re-fetches every field for every site
                      (name, address, coordinates, light pollution, site
                      type, parking/toilets heuristics, restricted access).
                      Intended to run monthly.
  --mode restricted   Re-fetches only the sites already in the cache and
                      updates just the `restricted_access` flag, leaving
                      every other field untouched. Cheaper than a full scrape
                      since it skips sitemap discovery. Intended to run
                      weekly.

Safety guarantees (matching scripts/refresh_forecast_cache.py):
- Writes to a .tmp file first; validates; atomically replaces the real file
  only after validation passes.
- Never overwrites a valid existing cache with an empty or badly-shrunk one.
- Preserves amenity fields (nearest_parking/nearest_toilets/amenities_fetched_at)
  written by scripts/refresh_amenities.py, since this script doesn't touch them.

Usage (from repo root):
    python scripts/refresh_sites_cache.py --mode full
    python scripts/refresh_sites_cache.py --mode restricted
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scraper  # noqa: E402

OUTPUT_FILE = REPO_ROOT / "sites_cache.json"
TMP_FILE = REPO_ROOT / "sites_cache.json.tmp"
MIN_COVERAGE = 0.90  # require at least 90% of the expected site count to be present
AMENITY_FIELDS = ("nearest_parking", "nearest_toilets", "amenities_fetched_at")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("refresh_sites_cache")


def validate_sites(sites: list, min_expected: int) -> tuple[bool, str]:
    if not sites:
        return False, "site list is empty"
    if len(sites) < min_expected * MIN_COVERAGE:
        return False, (
            f"only {len(sites)} sites, expected at least "
            f"{int(min_expected * MIN_COVERAGE)} ({MIN_COVERAGE:.0%} of {min_expected})"
        )
    if not all("slug" in s and "latitude" in s and "longitude" in s for s in sites):
        return False, "some sites are missing required fields"
    return True, f"{len(sites)} sites"


def atomic_write(sites: list, scraped_at: float | None = None) -> None:
    payload = {
        "scraped_at": scraped_at if scraped_at is not None else time.time(),
        "sites": sites,
    }
    TMP_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Re-read and validate the file we just wrote before replacing the real one.
    written = json.loads(TMP_FILE.read_bytes())
    ok, reason = validate_sites(written.get("sites", []), len(sites))
    if not ok:
        TMP_FILE.unlink(missing_ok=True)
        raise RuntimeError(f"Temp-file validation failed: {reason}")
    os.replace(TMP_FILE, OUTPUT_FILE)


def run_full() -> None:
    existing_sites, _ = scraper.load_cache()
    existing_by_slug = {s["slug"]: s for s in existing_sites}

    logger.info("Fetching all location slugs from sitemaps...")
    slugs = scraper.get_all_location_slugs()
    logger.info("Scraping %d candidate locations...", len(slugs))
    scraped = scraper.scrape_all(slugs, max_workers=4)

    baseline = len(existing_sites) or len(scraped)
    ok, reason = validate_sites(scraped, baseline)
    if not ok:
        logger.error("Full scrape failed validation (%s) — not writing cache.", reason)
        sys.exit(1)

    # Preserve amenity fields — they come from the separate Overpass-based
    # sweep (scripts/refresh_amenities.py), not from this scrape.
    for site in scraped:
        existing = existing_by_slug.get(site["slug"])
        if existing:
            for key in AMENITY_FIELDS:
                if key in existing:
                    site[key] = existing[key]

    atomic_write(scraped)
    logger.info("Full scrape complete: %d sites saved to %s.", len(scraped), OUTPUT_FILE)


def run_restricted() -> None:
    existing_sites, existing_scraped_at = scraper.load_cache()
    if not existing_sites:
        logger.error("sites_cache.json has no sites to refresh — run --mode full first.")
        sys.exit(1)

    slugs = [s["slug"] for s in existing_sites]
    logger.info("Re-checking restricted-access status for %d known sites...", len(slugs))
    refreshed = scraper.scrape_all(slugs, max_workers=4)
    refreshed_by_slug = {s["slug"]: s for s in refreshed}

    updated = 0
    for site in existing_sites:
        fresh = refreshed_by_slug.get(site["slug"])
        if fresh is not None:
            site["restricted_access"] = fresh["restricted_access"]
            updated += 1

    ok, reason = validate_sites(existing_sites, len(existing_sites))
    if not ok:
        logger.error("Restricted-access refresh failed validation (%s) — not writing cache.", reason)
        sys.exit(1)

    # Preserve the original scraped_at — this isn't a full-scrape event, so it
    # shouldn't reset the monthly full-scrape staleness clock.
    atomic_write(existing_sites, scraped_at=existing_scraped_at)
    logger.info(
        "Restricted-access refresh complete: %d/%d sites re-checked, %d saved to %s.",
        updated, len(slugs), len(existing_sites), OUTPUT_FILE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["full", "restricted"], required=True)
    args = parser.parse_args()

    logger.info("=== sites_cache.json refresh started (mode=%s) ===", args.mode)
    if args.mode == "full":
        run_full()
    else:
        run_restricted()
    logger.info("=== sites_cache.json refresh complete ===")


if __name__ == "__main__":
    main()
