#!/usr/bin/env python3
"""Validate forecast_cache.json before committing.

Exit 0 on success, 1 on failure.
"""

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORECAST_CACHE = REPO_ROOT / "forecast_cache.json"
SITES_CACHE = REPO_ROOT / "sites_cache.json"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"

errors = []
warnings = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    line = f"  [{tag}] {label}"
    if detail:
        line += f": {detail}"
    print(line)
    if not ok:
        errors.append(label)


def warn(label: str, detail: str = "") -> None:
    line = f"  [{WARN}] {label}"
    if detail:
        line += f": {detail}"
    print(line)
    warnings.append(label)


print(f"\nValidating {FORECAST_CACHE}\n")

# 1. File exists
if not FORECAST_CACHE.exists():
    print(f"  [{FAIL}] forecast_cache.json not found at {FORECAST_CACHE}")
    sys.exit(1)

# 2. Valid JSON
try:
    payload = json.loads(FORECAST_CACHE.read_text(encoding="utf-8"))
    check("Valid JSON", True)
except json.JSONDecodeError as exc:
    check("Valid JSON", False, str(exc))
    sys.exit(1)

# 3. Has 'data' key
check("Has 'data' key", "data" in payload)

# 4. data is a non-empty dict
data = payload.get("data", {})
check("data is non-empty dict", isinstance(data, dict) and len(data) > 0,
      f"{len(data)} sites" if isinstance(data, dict) else type(data).__name__)

if not data:
    print("\nFATAL: no forecast data — aborting validation.")
    sys.exit(1)

site_count = len(data)
print(f"\n  Forecast sites: {site_count}")

# 5. Timestamps
cached_at = payload.get("cached_at", 0)
generated_at = payload.get("generated_at", 0)
check("cached_at is a positive number", isinstance(cached_at, (int, float)) and cached_at > 0,
      str(cached_at))
check("generated_at is a positive number", isinstance(generated_at, (int, float)) and generated_at > 0,
      str(generated_at))

if cached_at > 0:
    age_h = (time.time() - float(cached_at)) / 3600
    print(f"  Cache age: {age_h:.1f}h")
    if age_h > 48:
        warn("Cache age > 48h — consider refreshing before committing")

# 6. generated_at_iso
gen_iso = payload.get("generated_at_iso", "")
if gen_iso:
    check("generated_at_iso present", True, gen_iso)
else:
    warn("generated_at_iso missing (will be derived on load)")

# 7. At least one site has non-empty forecast records
sample_slug = next(iter(data))
sample_records = data[sample_slug]
check("Sample site has forecast records", isinstance(sample_records, list) and len(sample_records) > 0,
      f"{sample_slug}: {len(sample_records)} records")

# 8. Compare against sites_cache.json if available
if SITES_CACHE.exists():
    try:
        sites_payload = json.loads(SITES_CACHE.read_text(encoding="utf-8"))
        if isinstance(sites_payload, list):
            all_sites = sites_payload
        elif isinstance(sites_payload, dict):
            all_sites = sites_payload.get("sites", [])
        else:
            all_sites = []

        if all_sites:
            expected = len(all_sites)
            coverage = site_count / expected
            check(
                f"Coverage >= 90% of sites_cache ({site_count}/{expected})",
                coverage >= 0.90,
                f"{coverage:.1%}",
            )
        else:
            warn("sites_cache.json has no 'sites' list — skipping coverage check")
    except Exception as exc:
        warn(f"Could not load sites_cache.json: {exc}")
else:
    warn("sites_cache.json not found — skipping coverage check")

# 9. site_timestamps
st = payload.get("site_timestamps", {})
check("site_timestamps present and non-empty", isinstance(st, dict) and len(st) > 0,
      f"{len(st)} entries")

# Summary
print()
if errors:
    print(f"RESULT: FAIL — {len(errors)} error(s): {', '.join(errors)}")
    sys.exit(1)
elif warnings:
    print(f"RESULT: PASS with {len(warnings)} warning(s): {', '.join(warnings)}")
else:
    print(f"RESULT: PASS — {site_count} sites, cache valid.")
