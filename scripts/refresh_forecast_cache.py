#!/usr/bin/env python3
"""Refresh forecast_cache.json at the repo root for Render deployment.

Architecture: This Windows PC fetches forecasts from Open-Meteo and writes
forecast_cache.json to the repo root.  The caller (refresh_and_push_forecasts.ps1)
then validates, commits, and pushes it to GitHub main.  Render auto-deploys.

Safety guarantees:
- Writes to a .tmp file first; validates; atomically replaces the real file only
  after validation passes.
- Never overwrites a valid existing cache with an empty or broken one.
- Exits non-zero on any fatal condition so the push script can detect and abort.

Usage (from repo root):
    python scripts/refresh_forecast_cache.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import weather  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FORECAST_LOOKAHEAD_DAYS = 14
OUTPUT_FILE = REPO_ROOT / "forecast_cache.json"
TMP_FILE    = REPO_ROOT / "forecast_cache.json.tmp"
SITES_FILE  = REPO_ROOT / "sites_cache.json"
MIN_COVERAGE = 0.90   # require at least 90 % of sites to have forecast data

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("refresh_forecast_cache")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_sites() -> list:
    """Load and validate sites from sites_cache.json at repo root.

    Supports these cache shapes:
        - direct list:        [{"slug": ..., "latitude": ..., "longitude": ...}, ...]
        - {"sites":   [...]}
        - {"data":    [...]}
        - {"results": [...]}
    """
    if not SITES_FILE.exists():
        logger.error("sites_cache.json not found at %s", SITES_FILE)
        sys.exit(1)

    try:
        raw = json.loads(SITES_FILE.read_bytes())
    except json.JSONDecodeError as exc:
        logger.error("sites_cache.json is not valid JSON: %s", exc)
        sys.exit(1)

    if isinstance(raw, list):
        sites = raw
    elif isinstance(raw, dict):
        for key in ("sites", "data", "results"):
            if isinstance(raw.get(key), list):
                sites = raw[key]
                break
        else:
            logger.error(
                "sites_cache.json is a dict but contains no 'sites', 'data', or 'results' list. "
                "Keys found: %s",
                list(raw.keys()),
            )
            sys.exit(1)
    else:
        logger.error(
            "Unsupported sites_cache.json format: %s", type(raw).__name__
        )
        sys.exit(1)

    # Validate required fields on each site
    valid = []
    skipped = 0
    for s in sites:
        if not isinstance(s, dict):
            skipped += 1
            continue
        if not all(k in s for k in ("slug", "latitude", "longitude")):
            skipped += 1
            continue
        valid.append(s)

    if skipped:
        logger.warning(
            "%d sites skipped: missing slug/latitude/longitude.", skipped
        )

    if not valid:
        logger.error("No valid sites found in sites_cache.json.")
        sys.exit(1)

    logger.info("Loaded %d valid sites from %s.", len(valid), SITES_FILE)
    return valid


def load_existing_cache() -> tuple:
    """Return (existing_data_dict, existing_site_timestamps_dict).

    Returns ({}, {}) on any failure — the refresh will proceed without merging.
    """
    try:
        payload = json.loads(OUTPUT_FILE.read_bytes())
        data = {
            slug: [tuple(x) for x in records]
            for slug, records in payload.get("data", {}).items()
        }
        site_ts = {
            slug: float(t)
            for slug, t in payload.get("site_timestamps", {}).items()
        }
        age_h = (time.time() - float(payload.get("generated_at", 0))) / 3600
        logger.info(
            "Existing cache: %d sites, generated %.1fh ago.", len(data), age_h
        )
        return data, site_ts
    except FileNotFoundError:
        logger.info("No existing forecast cache found — will create fresh.")
    except Exception as exc:
        logger.warning("Could not load existing cache: %s", exc)
    return {}, {}


def validate_payload(payload: dict, total_sites: int) -> tuple:
    """Return (ok: bool, reason: str).

    Checks that the payload is safe to write and commit.
    """
    data = payload.get("data", {})
    if not isinstance(data, dict) or len(data) == 0:
        return False, "data dict is empty or missing"
    if not payload.get("generated_at"):
        return False, "missing generated_at timestamp"
    if total_sites > 0:
        coverage = len(data) / total_sites
        if coverage < MIN_COVERAGE:
            return (
                False,
                f"coverage {coverage:.1%} below {MIN_COVERAGE:.0%} threshold "
                f"({len(data)}/{total_sites} sites)",
            )
    return True, f"{len(data)} sites"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== Forecast cache refresh started ===")
    logger.info("Repo root : %s", REPO_ROOT)
    logger.info("Output    : %s", OUTPUT_FILE)

    all_sites = load_sites()
    existing_data, existing_site_ts = load_existing_cache()

    today    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today + timedelta(days=FORECAST_LOOKAHEAD_DAYS)
    logger.info(
        "Fetching %d sites: %s to %s. "
        "Full sweep takes ~35 min at the conservative rate limit.",
        len(all_sites), today.date(), end_date.date(),
    )

    # Abort early if rate-limited so we do not burn a long wait window
    rl = weather.rate_limit_status()
    if rl["active"]:
        logger.warning(
            "Open-Meteo rate limit active (%.0f min remaining). "
            "Keeping existing cache unchanged.",
            rl["retry_after_seconds"] / 60,
        )
        sys.exit(0)

    try:
        new_data = weather.get_full_forecast_background(all_sites, today, end_date)
    except Exception as exc:
        logger.error("Forecast fetch failed unexpectedly: %s", exc)
        logger.info("Keeping existing cache unchanged.")
        sys.exit(1)

    logger.info("Fetched %d / %d sites.", len(new_data), len(all_sites))

    if not new_data:
        logger.error(
            "No forecast data returned — aborting to protect existing cache."
        )
        sys.exit(1)

    # Merge new data on top of existing (preserves sites not re-fetched)
    now = time.time()
    merged_data    = {**existing_data, **new_data}
    merged_site_ts = {**existing_site_ts, **{slug: now for slug in new_data}}

    output_payload = {
        "cached_at":         now,
        "generated_at":      now,
        "generated_at_iso":  datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "data":              {slug: list(records) for slug, records in merged_data.items()},
        "site_timestamps":   merged_site_ts,
        "last_requested_at": {},
    }

    # Validate in memory before touching disk
    ok, reason = validate_payload(output_payload, len(all_sites))
    if not ok:
        logger.error(
            "In-memory validation failed (%s) — not writing cache.", reason
        )
        sys.exit(1)

    # Atomic write: .tmp -> validate on disk -> os.replace
    try:
        TMP_FILE.write_text(
            json.dumps(output_payload, ensure_ascii=False), encoding="utf-8"
        )
        written = json.loads(TMP_FILE.read_bytes())
        ok2, reason2 = validate_payload(written, len(all_sites))
        if not ok2:
            logger.error(
                "Temp-file validation failed (%s) — aborting.", reason2
            )
            TMP_FILE.unlink(missing_ok=True)
            sys.exit(1)

        os.replace(TMP_FILE, OUTPUT_FILE)
    except Exception as exc:
        logger.error("Failed to write forecast cache: %s", exc)
        TMP_FILE.unlink(missing_ok=True)
        sys.exit(1)

    # Final summary
    size_mb  = OUTPUT_FILE.stat().st_size / 1_000_000
    coverage = len(merged_data) / len(all_sites) * 100

    print()
    print("=== Forecast cache refresh complete ===")
    print(f"  Total sites:           {len(all_sites)}")
    print(f"  Forecast sites cached: {len(merged_data)}")
    print(f"  Coverage:              {coverage:.1f}%")
    print(f"  Generated at (ISO):    {output_payload['generated_at_iso']}")
    print(f"  File size:             {size_mb:.1f} MB")
    print(f"  Output path:           {OUTPUT_FILE}")
    print()


if __name__ == "__main__":
    main()
