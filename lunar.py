"""
lunar.py — Lunar illumination calculator (no external dependencies).

Uses the Julian Day Number method with a known reference new moon to compute
the Moon's age and illumination fraction for any given date/datetime.

Reference new moon: 6 January 2000 18:14 UTC  (JD 2451549.5 + 0.76 ≈ 2451550.26)
Synodic period: 29.53058867 days
"""

import math
from datetime import date, datetime

_REFERENCE_NEW_MOON_JD: float = 2451550.26  # 6 Jan 2000 18:14 UTC
_SYNODIC_PERIOD: float = 29.53058867  # days


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def _julian_day(dt: date | datetime) -> float:
    """Convert a date or datetime to a Julian Day Number."""
    y, m, d = dt.year, dt.month, dt.day
    frac = 0.5  # default: noon
    if isinstance(dt, datetime):
        frac = (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0

    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5 + frac


def moon_age(dt: date | datetime) -> float:
    """Days since last new moon (0 = new moon, ~14.77 = full moon)."""
    jd = _julian_day(dt)
    return (jd - _REFERENCE_NEW_MOON_JD) % _SYNODIC_PERIOD


def lunar_illumination(dt: date | datetime) -> float:
    """
    Fraction of the Moon's disc that is illuminated, as a percentage.
    0 % = new moon (ideal for stargazing), 100 % = full moon (worst).
    """
    age = moon_age(dt)
    fraction = (1.0 - math.cos(2.0 * math.pi * age / _SYNODIC_PERIOD)) / 2.0
    return round(fraction * 100.0, 1)


# ---------------------------------------------------------------------------
# Human-readable helpers
# ---------------------------------------------------------------------------

def moon_emoji(age: float) -> str:
    """Unicode moon-phase emoji from moon age in days."""
    phase = age / _SYNODIC_PERIOD  # 0–1
    if phase < 0.0625 or phase >= 0.9375:
        return "🌑"
    elif phase < 0.1875:
        return "🌒"
    elif phase < 0.3125:
        return "🌓"
    elif phase < 0.4375:
        return "🌔"
    elif phase < 0.5625:
        return "🌕"
    elif phase < 0.6875:
        return "🌖"
    elif phase < 0.8125:
        return "🌗"
    else:
        return "🌘"


def moon_phase_name(age: float) -> str:
    """Descriptive phase name from moon age in days."""
    illum = (1.0 - math.cos(2.0 * math.pi * age / _SYNODIC_PERIOD)) / 2.0 * 100.0
    waxing = age < _SYNODIC_PERIOD / 2.0

    if illum < 3:
        return "New Moon"
    if illum > 97:
        return "Full Moon"
    if illum < 50:
        return "Waxing Crescent" if waxing else "Waning Crescent"
    if illum > 50:
        return "Waxing Gibbous" if waxing else "Waning Gibbous"
    return "First Quarter" if waxing else "Last Quarter"


def lunar_quality(illumination_pct: float) -> str:
    """Stargazing quality label for the given lunar illumination."""
    if illumination_pct < 15:
        return "excellent"
    if illumination_pct < 40:
        return "good"
    if illumination_pct < 65:
        return "fair"
    if illumination_pct < 85:
        return "poor"
    return "very poor"


def lunar_info(dt: date | datetime) -> dict:
    """Return a complete lunar info dict for the given date."""
    age = moon_age(dt)
    illum = lunar_illumination(dt)
    return {
        "illumination_pct": illum,
        "phase_name": moon_phase_name(age),
        "emoji": moon_emoji(age),
        "quality": lunar_quality(illum),
    }
