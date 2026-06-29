#!/usr/bin/env python3
"""Local forecast refresh script for Windows self-hosted deployment.

Run by Windows Task Scheduler every 6–12 hours.

Safety guarantees:
- Writes to forecast_cache.tmp.json first; validates; then atomically replaces forecast_cache.json.
- Never overwrites a good existing cache with empty or broken data.
- On timeout or rate limit, exits gracefully and keeps the previous cache.
- Logs to DATA_DIR/logs/forecast_refresh.log.

Usage (from repo root):
    python scripts/refresh_forecasts_local.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import DATA_DIR   # noqa: E402
import weather                # noqa: E402

# ---------------------------------------------------------------------------
# Logging — both console and a rolling log file in DATA_DIR/logs/
# ---------------------------------------------------------------------------
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "forecast_refresh.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("refresh_forecasts")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FORECAST_LOOKAHEAD_DAYS = 14
OUTPUT_FILE = DATA_DIR / "forecast_cache.json"
TMP_FILE    = DATA_DIR / "forecast_cache.tmp.json"

# Sites cache: prefer DATA_DIR copy, fall back to repo root (dev convenience)
_SITES_CANDIDATES = [DATA_DIR / "sites_cache.json", REPO_ROOT / "sites_cache.json"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_existing_cache() -> dict:
    """Load existing forecast cache from disk (returns {} on any failure)."""
    try:
        with open(OUTPUT_FILE) as fh:
            payload = json.load(fh)
        age_h = (time.time() - float(payload.get("generated_at", 0))) / 3600
        logger.info(
            "Existing cache: %d sites, generated %.1fh ago.",
            len(payload.get("data", {})), age_h,
        )
        return payload
    except FileNotFoundError:
        logger.info("No existing forecast cache found — will create fresh.")
    except Exception as exc:
        logger.warning("Could not load existing cache: %s", exc)
    return {}


def _load_all_sites() -> list[dict]:
    """Load stargazing sites from sites_cache.json."""
    for path in _SITES_CANDIDATES:
        if not path.exists():
            continue
        try:
            with open(path) as fh:
                payload = json.load(fh)
            sites = payload.get("sites", [])
            if sites:
                logger.info("Loaded %d sites from %s.", len(sites), path)
                return sites
        except Exception as exc:
            logger.warning("Could not load sites from %s: %s", path, exc)
    logger.error(
        "No sites_cache.json found. Start the app at least once so it can "
        "build the site list, then re-run this script."
    )
    return []


def _validate(payload: dict) -> tuple[bool, str]:
    """Return (ok, reason). Checks the payload is a usable forecast cache."""
    data = payload.get("data", {})
    if not isinstance(data, dict) or len(data) == 0:
        return False, "data dict is empty or missing"
    if not payload.get("generated_at"):
        return False, "missing generated_at timestamp"
    site_count = payload.get("site_count", len(data))
    if site_count == 0:
        return False, "site_count is 0"
    return True, f"{len(data)} sites, site_count={site_count}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== Forecast refresh started ===")
    logger.info("DATA_DIR : %s", DATA_DIR)
    logger.info("Output   : %s", OUTPUT_FILE)

    existing_payload = _load_existing_cache()
    existing_data = {
        slug: [tuple(x) for x in records]
        for slug, records in existing_payload.get("data", {}).items()
    }
    existing_site_ts = {
        slug: float(t)
        for slug, t in existing_payload.get("site_timestamps", {}).items()
    }

    all_sites = _load_all_sites()
    if not all_sites:
        sys.exit(1)

    today    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today + timedelta(days=FORECAST_LOOKAHEAD_DAYS)
    logger.info(
        "Fetching %d sites: %s → %s  (this takes ~35 min for a full site list)",
        len(all_sites), today.date(), end_date.date(),
    )

    # Check rate limit before starting
    rl = weather.rate_limit_status()
    if rl["active"]:
        logger.warning(
            "Open-Meteo rate limit active — %.0f min remaining. Keeping existing cache.",
            rl["retry_after_seconds"] / 60,
        )
        sys.exit(0)

    # Checkpoint callback: called every BACKGROUND_CHECKPOINT_EVERY successful batches
    def on_checkpoint(partial: dict) -> None:
        logger.info("Checkpoint: %d/%d sites fetched so far.", len(partial), len(all_sites))

    try:
        new_data = weather.get_full_forecast_background(
            all_sites, today, end_date, on_batch_complete=on_checkpoint
        )
    except Exception as exc:
        logger.error("Forecast fetch failed unexpectedly: %s", exc)
        logger.info("Keeping existing cache unchanged.")
        sys.exit(1)

    logger.info("Fetched %d / %d sites successfully.", len(new_data), len(all_sites))

    if not new_data:
        logger.warning("No forecast data returned — keeping existing cache.")
        sys.exit(1)

    # Merge new data over existing (preserves data for sites not re-fetched)
    now = time.time()
    merged_data    = {**existing_data, **new_data}
    merged_site_ts = {**existing_site_ts, **{slug: now for slug in new_data}}

    output_payload = {
        "generated_at":     now,
        "generated_at_iso": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cached_at":        now,
        "site_count":       len(merged_data),
        "forecast_days":    FORECAST_LOOKAHEAD_DAYS,
        "source":           "open-meteo",
        "data":             {slug: list(records) for slug, records in merged_data.items()},
        "site_timestamps":  merged_site_ts,
    }

    # Validate in-memory first
    ok, reason = _validate(output_payload)
    if not ok:
        logger.error("In-memory validation failed (%s) — not writing cache.", reason)
        sys.exit(1)

    # Write → validate on disk → atomic replace
    try:
        with open(TMP_FILE, "w") as fh:
            json.dump(output_payload, fh)

        with open(TMP_FILE) as fh:
            written = json.load(fh)
        ok2, reason2 = _validate(written)
        if not ok2:
            logger.error("Temp-file validation failed (%s) — aborting.", reason2)
            TMP_FILE.unlink(missing_ok=True)
            sys.exit(1)

        os.replace(TMP_FILE, OUTPUT_FILE)
        size_mb = OUTPUT_FILE.stat().st_size / 1_000_000
        logger.info(
            "forecast_cache.json updated: %d sites (%.1f MB). Newly fetched: %d.",
            len(merged_data), size_mb, len(new_data),
        )
    except Exception as exc:
        logger.error("Failed to write forecast cache: %s", exc)
        TMP_FILE.unlink(missing_ok=True)
        sys.exit(1)

    logger.info("=== Forecast refresh complete ===")


if __name__ == "__main__":
    main()
