"""
lunar.py — Lunar illumination calculator (no external dependencies).

Uses the Julian Day Number method with a known reference new moon to compute
the Moon's age and illumination fraction for any given date/datetime.

Reference new moon: 6 January 2000 18:14 UTC  (JD 2451549.5 + 0.76 ≈ 2451550.26)
Synodic period: 29.53058867 days
"""

import math
from datetime import date, datetime, timedelta

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


# ---------------------------------------------------------------------------
# Moonrise / moonset
#
# The phase math above only needs the calendar date. Rise/set needs the
# Moon's actual sky position (RA/Dec) at a given instant and place, which
# needs real orbital mechanics — the Moon moves ~13 deg/day, too fast for a
# sunrise-style single-pass formula to stay accurate. This uses Meeus/
# Schlyter's low-precision lunar theory (accurate to ~1-2 arcmin, i.e. a
# minute or two of rise/set time — plenty for a UI timeline) to keep the
# project dependency-free, matching the hand-rolled solar calculation in
# app.py's _sun_event_utc.
# ---------------------------------------------------------------------------

def _julian_day_precise(dt: datetime) -> float:
    """Like _julian_day, but always uses dt's exact time (dt must be tz-aware
    UTC or naive-and-already-UTC — moonrise/set needs real instants, not the
    date-only noon default the phase functions use)."""
    y, m, d = dt.year, dt.month, dt.day
    frac = (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5 + frac


def _norm360(deg: float) -> float:
    return deg % 360.0


def _moon_ecliptic_position(jd: float) -> tuple[float, float, float]:
    """Geocentric ecliptic (longitude_deg, latitude_deg, distance_earth_radii)
    of the Moon at the given Julian Day. Unperturbed two-body orbit plus the
    dozen largest periodic perturbation terms (evection, variation, yearly
    equation, ...) — Schlyter's well-known low-precision reduction of Meeus's
    fuller series."""
    d = jd - 2451543.5  # days since 2000 Jan 0.0 UT

    N = _norm360(125.1228 - 0.0529538083 * d)   # longitude of ascending node
    i = 5.1454                                   # inclination
    w = _norm360(318.0634 + 0.1643573223 * d)    # argument of perigee
    a = 60.2666                                  # mean distance, Earth radii
    e = 0.054900                                 # eccentricity
    M = _norm360(115.3654 + 13.0649929509 * d)   # mean anomaly

    Ms = _norm360(356.0470 + 0.9856002585 * d)   # Sun's mean anomaly
    ws = _norm360(282.9404 + 4.70935e-5 * d)     # Sun's argument of perihelion
    Ls = _norm360(Ms + ws)                       # Sun's mean longitude

    # Kepler's equation, solved iteratively (converges in a couple of steps
    # at this eccentricity).
    E = M + math.degrees(e * math.sin(math.radians(M)) * (1 + e * math.cos(math.radians(M))))
    for _ in range(6):
        delta = (E - math.degrees(e * math.sin(math.radians(E))) - M) / (1 - e * math.cos(math.radians(E)))
        E -= delta
        if abs(delta) < 1e-6:
            break

    xv = a * (math.cos(math.radians(E)) - e)
    yv = a * (math.sqrt(1 - e * e) * math.sin(math.radians(E)))
    r = math.hypot(xv, yv)
    v = math.degrees(math.atan2(yv, xv))  # true anomaly

    i_r = math.radians(i)
    vw = math.radians(v + w)
    N_r = math.radians(N)
    xh = r * (math.cos(N_r) * math.cos(vw) - math.sin(N_r) * math.sin(vw) * math.cos(i_r))
    yh = r * (math.sin(N_r) * math.cos(vw) + math.cos(N_r) * math.sin(vw) * math.cos(i_r))
    zh = r * (math.sin(vw) * math.sin(i_r))

    lon = _norm360(math.degrees(math.atan2(yh, xh)))
    lat = math.degrees(math.atan2(zh, math.hypot(xh, yh)))

    Lm = _norm360(N + w + M)  # Moon's mean longitude
    D = Lm - Ls                # mean elongation from the Sun
    F = Lm - N                 # argument of latitude

    def s(x: float) -> float:
        return math.sin(math.radians(x))

    lon_corr = (
        -1.274 * s(M - 2 * D)          # evection
        + 0.658 * s(2 * D)              # variation
        - 0.186 * s(Ms)                 # yearly equation
        - 0.059 * s(2 * M - 2 * D)
        - 0.057 * s(M - 2 * D + Ms)
        + 0.053 * s(M + 2 * D)
        + 0.046 * s(2 * D - Ms)
        + 0.041 * s(M - Ms)
        - 0.035 * s(D)                  # parallactic equation
        - 0.031 * s(M + Ms)
        - 0.015 * s(2 * F - 2 * D)
        + 0.011 * s(M - 4 * D)
    )
    lat_corr = (
        -0.173 * s(F - 2 * D)
        - 0.055 * s(M - F - 2 * D)
        - 0.046 * s(M + F - 2 * D)
        + 0.033 * s(F + 2 * D)
        + 0.017 * s(2 * M + F)
    )

    def c(x: float) -> float:
        return math.cos(math.radians(x))

    dist_corr = -0.58 * c(M - 2 * D) - 0.46 * c(2 * D)

    return _norm360(lon + lon_corr), lat + lat_corr, r + dist_corr


def _ecliptic_to_equatorial(lon_deg: float, lat_deg: float, jd: float) -> tuple[float, float]:
    """(lon, lat) geocentric ecliptic -> (RA, Dec) equatorial, both degrees."""
    d = jd - 2451543.5
    ecl = math.radians(23.4393 - 3.563e-7 * d)  # mean obliquity of the ecliptic
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    xe = math.cos(lon) * math.cos(lat)
    ye = math.sin(lon) * math.cos(lat)
    ze = math.sin(lat)
    xeq = xe
    yeq = ye * math.cos(ecl) - ze * math.sin(ecl)
    zeq = ye * math.sin(ecl) + ze * math.cos(ecl)
    ra = _norm360(math.degrees(math.atan2(yeq, xeq)))
    dec = math.degrees(math.atan2(zeq, math.hypot(xeq, yeq)))
    return ra, dec


def moon_altitude_deg(dt: datetime, latitude: float, longitude: float) -> float:
    """Moon's altitude above its rise/set threshold, in degrees (>0 = up).
    The threshold accounts for atmospheric refraction and the Moon's
    horizontal parallax (which — unlike for the Sun — is too large to ignore
    at the Moon's distance): h0 = 0.7275*parallax - 34' (Meeus, ch.15)."""
    jd = _julian_day_precise(dt)
    lon, lat, r = _moon_ecliptic_position(jd)
    ra, dec = _ecliptic_to_equatorial(lon, lat, jd)

    gst = _norm360(280.46061837 + 360.98564736629 * (jd - 2451545.0))
    lst = _norm360(gst + longitude)  # longitude: east positive, matches geocoder output
    ha = ((lst - ra + 180) % 360) - 180

    alt = math.degrees(math.asin(
        math.sin(math.radians(latitude)) * math.sin(math.radians(dec))
        + math.cos(math.radians(latitude)) * math.cos(math.radians(dec)) * math.cos(math.radians(ha))
    ))
    parallax_deg = math.degrees(math.asin(1.0 / r))
    h0 = 0.7275 * parallax_deg - 34.0 / 60.0
    return alt - h0


def moon_rise_set_utc(window_start: datetime, window_end: datetime, latitude: float, longitude: float) -> dict:
    """Moonrise/moonset within [window_start, window_end] (tz-aware UTC
    datetimes — e.g. one night's sunset through the next sunrise).

    Within any window shorter than a lunar day (~24h50m) the Moon's single
    continuous up-arc crosses the horizon at most once going up and once
    going down, so there's at most one moonrise and one moonset to find.
    Coarse-scans hourly for a sign change in altitude, then bisects each
    bracket down to sub-minute precision.

    Returns {"moonrise": datetime|None, "moonset": datetime|None,
             "up_at_start": bool, "up_at_end": bool} — combine these the
    same way the caller combines sunset/sunrise: a bracket with no rise
    but up_at_start=True means the Moon was already up when the window
    began, etc.
    """
    total_seconds = (window_end - window_start).total_seconds()
    n_samples = max(8, int(total_seconds / 3600) + 1)  # ~hourly
    step = timedelta(seconds=total_seconds / n_samples)
    times = [window_start + i * step for i in range(n_samples + 1)]
    alts = [moon_altitude_deg(t, latitude, longitude) for t in times]

    def refine(t_lo: datetime, t_hi: datetime, rising: bool) -> datetime:
        for _ in range(16):  # halves an ~hour bracket to well under a second
            t_mid = t_lo + (t_hi - t_lo) / 2
            a_mid = moon_altitude_deg(t_mid, latitude, longitude)
            if rising:
                if a_mid <= 0:
                    t_lo = t_mid
                else:
                    t_hi = t_mid
            else:
                if a_mid > 0:
                    t_lo = t_mid
                else:
                    t_hi = t_mid
        return t_lo + (t_hi - t_lo) / 2

    moonrise = moonset = None
    for idx in range(len(alts) - 1):
        if alts[idx] <= 0 < alts[idx + 1]:
            moonrise = refine(times[idx], times[idx + 1], True)
        elif alts[idx] > 0 >= alts[idx + 1]:
            moonset = refine(times[idx], times[idx + 1], False)

    return {
        "moonrise": moonrise,
        "moonset": moonset,
        "up_at_start": alts[0] > 0,
        "up_at_end": alts[-1] > 0,
    }
