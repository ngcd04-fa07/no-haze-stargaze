"""
scraper.py — Fetches and caches stargazing site data from gostargazing.co.uk

Data extracted per site:
  - name, slug, URL
  - address (raw text)
  - latitude / longitude (from DarkSkySites embedded link)
  - light_pollution: dark site | rural | semi-rural | suburban | urban
  - site_type: Dark Sky Discovery Site | Go Stargazing Site | Recommended Stargazing Site | Aurora Viewpoint
"""

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://gostargazing.co.uk"
CACHE_FILE = Path(__file__).parent / "sites_cache.json"
CACHE_MAX_AGE_HOURS = 168  # 1 week
CACHE_MAX_AGE_SECONDS = CACHE_MAX_AGE_HOURS * 3600

SITEMAP_URLS = [
    f"{BASE_URL}/location-sitemap.xml",
    f"{BASE_URL}/location-sitemap2.xml",
    f"{BASE_URL}/location-sitemap3.xml",
]

HEADERS = {
    "User-Agent": (
        "StargazingRecommender/1.0 (educational; "
        "https://github.com/example/stargazing)"
    )
}

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

LIGHT_POLLUTION_LEVELS = ["dark site", "rural", "semi-rural", "suburban", "urban"]

SITE_TYPES = [
    "Dark Sky Discovery Site",
    "Recommended Stargazing Site",
    "Go Stargazing Site",
    "Aurora Viewpoint",
]

# Slugs that are clearly not outdoor stargazing locations
_EXCLUDE_SLUG_PATTERNS = re.compile(
    r"(school|library|museum|theatre|theater|pub|inn|hotel|"
    r"community-centre|village-hall|church|shopping|arena|"
    r"university|college|academy|aquatics|cinema|gallery|"
    r"brewery|distillery|cafe|bistro|restaurant|farm-shop|"
    r"caravan|camping-site|glamping|retreat|glamping|lodge|"
    r"cottage|guest-house|b-and-b|holiday|hostel|"
    r"observatory|planetarium)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Sitemap helpers
# ---------------------------------------------------------------------------

def _fetch_sitemap_slugs(sitemap_url: str) -> list[str]:
    """Return all location slugs found in one sitemap XML file."""
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch sitemap %s: %s", sitemap_url, exc)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.error("Failed to parse sitemap %s: %s", sitemap_url, exc)
        return []

    slugs = []
    for url_el in root.findall(f"{{{SITEMAP_NS}}}url"):
        loc_el = url_el.find(f"{{{SITEMAP_NS}}}loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        # Only proper location pages (not the index page)
        if "/locations/" in loc and not loc.endswith("/locations/"):
            slug = loc.rstrip("/").split("/locations/")[-1]
            if slug:
                slugs.append(slug)

    return slugs


def get_all_location_slugs() -> list[str]:
    """Return deduplicated list of all location slugs from all sitemaps."""
    seen: set[str] = set()
    slugs: list[str] = []
    for url in SITEMAP_URLS:
        logger.info("Fetching sitemap: %s", url)
        for slug in _fetch_sitemap_slugs(url):
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
    logger.info("Total unique location slugs: %d", len(slugs))
    return slugs


# ---------------------------------------------------------------------------
# Individual site scraping
# ---------------------------------------------------------------------------

def _extract_coords(html_text: str) -> tuple[float, float] | None:
    """Extract lat/lng from the DarkSkySites embedded link."""
    match = re.search(
        r"darkskysites\.com/\?lat=([-\d.]+)&(?:amp;)?lng=([-\d.]+)",
        html_text,
    )
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except ValueError:
        return None


def _extract_light_pollution(html_text: str) -> str:
    """
    Extract light pollution level from the raw HTML.

    The level word sits inside an <a> tag:
      similar to a <a ...>rural</a> location
    So we match through any optional HTML tags between words.
    """
    match = re.search(
        r"similar to (?:a |an )?(?:<[^>]+>)?"
        r"(dark site|rural|semi-rural|suburban|urban)"
        r"(?:</[^>]+>)?\s+location",
        html_text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).lower()
    return "unknown"


def _extract_site_type(text: str) -> str:
    """Extract site classification from page text."""
    patterns = [
        ("Dark Sky Discovery Site",   r"is an? Dark Sky Discovery Site"),
        ("Recommended Stargazing Site", r"is an? Recommended Stargazing Site"),
        ("Go Stargazing Site",         r"is an? Go Stargazing Site"),
        ("Aurora Viewpoint",           r"is an? Aurora Viewpoint"),
    ]
    for label, pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "Unknown"


# Keywords in site names that imply toilets are likely present
_TOILET_NAME_KEYWORDS = (
    "visitor centre", "visitor center", "museum", "planetarium",
    "observatory", "café", "cafe", "pub", "inn", "hotel", "castle",
    "heritage centre", "garden centre", "arts centre", "country park",
    "leisure centre", "nature centre", "community centre", "learning centre",
    "national park centre",
)

# Keywords in site names / slugs that imply parking exists
_PARKING_SLUG_KEYWORDS = ("car-park", "car park", "parking", "park-ride")


def _extract_parking(name: str, slug: str, html_text: str) -> bool:
    """
    Return True if the site is likely to have (on-site or adjacent) car parking.

    Logic (in order of confidence):
    1. "car park" / "car-park" in the name or slug.
    2. Go Stargazing Sites always offer "a safe place to park".
    3. Dark Sky Discovery Sites boilerplate says "most… have overnight parking".
    4. Positive parking keyword in the site's intro/meta description.
    """
    name_l = name.lower()
    slug_l = slug.lower()

    if any(kw in name_l or kw in slug_l for kw in _PARKING_SLUG_KEYWORDS):
        return True

    # Go Stargazing Site validation text
    if re.search(r"offers? a safe place to park", html_text, re.IGNORECASE):
        return True

    # Dark Sky Discovery Site boilerplate (high hit-rate for DSDS)
    if re.search(
        r"most such locations have overnight parking",
        html_text, re.IGNORECASE,
    ):
        return True

    # Explicit positive mention in the site description or meta
    if re.search(
        r"(free car parking|paid (car )?parking|pay.and.display"
        r"|parking (is |all year|available|on.?site|on the site)"
        r"|car park.{0,30}(free|paid|available|nearby)"
        r"|ample parking|plenty of parking)",
        html_text,
        re.IGNORECASE,
    ):
        return True

    return False


def _extract_toilets(name: str, slug: str, html_text: str) -> bool | None:
    """
    Return True if toilets are likely present, False if explicitly absent,
    None if unknown.

    Priority order:
    1. Explicit positive mention in the intro text or meta description.
    2. Explicit negative mention ("no toilet facilities").
    3. Site name implies facilities (visitor centre, museum, pub, etc.).
    4. Otherwise None (unknown).
    """
    name_l = name.lower()

    # --- Extract the site-specific intro paragraph ---
    intro_match = re.search(
        r'class="loc-Intro_Text[^"]*"[^>]*>(.*?)</(?:p|div)\s*>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    intro = intro_match.group(1) if intro_match else ""
    intro_clean = re.sub(r"<[^>]+>", " ", intro).strip().lower()

    # Positive explicit mention
    if re.search(
        r"(toilets?|washrooms?|wc)\b.{0,50}"
        r"(available|on.?site|near(by)?|provided|access|open)",
        intro_clean, re.IGNORECASE,
    ):
        return True

    # Also check the meta description
    meta_m = re.search(
        r'<meta[^>]+(?:name="description"|property="og:description")[^>]+'
        r'content="([^"]*)"',
        html_text, re.IGNORECASE,
    )
    meta = meta_m.group(1).lower() if meta_m else ""
    if re.search(
        r"(toilets?|washrooms?|wc)\b",
        meta, re.IGNORECASE,
    ) and "may or may not" not in meta:
        return True

    # Explicit negative
    if re.search(
        r"no (toilet|wc|washroom|toilet facilit)",
        intro_clean + " " + meta, re.IGNORECASE,
    ):
        return False

    # Infer from well-known amenity-bearing site types
    if any(kw in name_l for kw in _TOILET_NAME_KEYWORDS):
        return True

    return None  # Unknown


def _extract_address(soup: BeautifulSoup) -> str:
    """Best-effort address extraction from a location page."""
    # The address usually lives just below the <h1> as a paragraph or subtitle.
    h1 = soup.find("h1")
    if h1:
        # Walk siblings / next elements
        for sibling in h1.next_siblings:
            if not hasattr(sibling, "get_text"):
                continue
            txt = sibling.get_text(" ", strip=True)
            # Looks like an address if it contains commas and a postcode or region
            if (
                "," in txt
                and 5 < len(txt) < 300
                and (
                    re.search(r"\b[A-Z]{1,2}\d", txt)  # postcode prefix
                    or any(w in txt for w in ["England", "Scotland", "Wales", "Ireland"])
                )
            ):
                return txt
    return ""


def scrape_location(slug: str) -> dict | None:
    """Fetch and parse a single location page. Returns site dict or None."""
    url = f"{BASE_URL}/locations/{slug}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("Error fetching %s: %s", url, exc)
        return None

    # Coordinates are essential; skip if not found
    coords = _extract_coords(resp.text)
    if coords is None:
        return None

    lat, lng = coords
    # Sanity-check: must be roughly within the British Isles bounding box
    if not (49.0 <= lat <= 61.0 and -11.0 <= lng <= 2.5):
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    page_text = soup.get_text(" ")

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else slug.replace("-", " ").title()
    address = _extract_address(soup)
    # Pass raw HTML for pollution (level is inside an <a> tag)
    light_pollution = _extract_light_pollution(resp.text)
    site_type = _extract_site_type(page_text)
    has_parking = _extract_parking(name, slug, resp.text)
    has_toilets = _extract_toilets(name, slug, resp.text)

    return {
        "name": name,
        "slug": slug,
        "url": url,
        "address": address,
        "latitude": lat,
        "longitude": lng,
        "light_pollution": light_pollution,
        "site_type": site_type,
        "has_parking": has_parking,
        "has_toilets": has_toilets,   # True | False | None
    }


# ---------------------------------------------------------------------------
# Batch scraper
# ---------------------------------------------------------------------------

def scrape_all(
    slugs: list[str],
    max_workers: int = 4,
    delay_per_worker: float = 0.8,
    progress_cb=None,
    site_found_cb=None,
) -> list[dict]:
    """Scrape all slugs concurrently and return list of site dicts."""
    sites: list[dict] = []
    total = len(slugs)

    def worker(slug):
        site = scrape_location(slug)
        time.sleep(delay_per_worker)
        return site

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, s): s for s in slugs}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
                if result:
                    sites.append(result)
                    if site_found_cb:
                        site_found_cb(result, sites)
            except Exception as exc:
                logger.debug("Worker error: %s", exc)
            if progress_cb:
                progress_cb(done, total, len(sites))

    return sites


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def save_cache(sites: list[dict], scraped_at: float | None = None) -> None:
    """Write sites to cache. Pass scraped_at to preserve the original timestamp
    (e.g. when only amenity data was updated, not the site list itself)."""
    payload = {"scraped_at": scraped_at if scraped_at is not None else time.time(),
               "sites": sites}
    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    logger.info("Saved %d sites to cache.", len(sites))


def load_cache() -> tuple[list[dict], float]:
    """Return (sites, scraped_at_timestamp). sites=[] if cache missing."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload.get("sites", []), payload.get("scraped_at", 0.0)
    except (FileNotFoundError, json.JSONDecodeError):
        return [], 0.0


def is_cache_fresh(scraped_at: float, max_age_hours: float = CACHE_MAX_AGE_HOURS) -> bool:
    return scraped_at > 0 and (time.time() - scraped_at) < max_age_hours * 3600


def get_sites(force_refresh: bool = False) -> list[dict]:
    """Load cached sites; trigger a fresh scrape if cache is stale or missing."""
    sites, scraped_at = load_cache()
    if not force_refresh and is_cache_fresh(scraped_at) and sites:
        logger.info("Using %d cached sites (scraped %.1fh ago).",
                    len(sites), (time.time() - scraped_at) / 3600)
        return sites

    logger.info("Cache missing or stale — starting full scrape.")
    slugs = get_all_location_slugs()
    sites = scrape_all(slugs)
    if sites:
        save_cache(sites)
    return sites
