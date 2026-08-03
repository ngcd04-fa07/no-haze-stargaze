"""
commons_photos.py — Nearest landscape photo for a site, via Wikimedia Commons.

Wikimedia Commons' geosearch API is used instead of Geograph's own API
because it's fully keyless (Geograph's requires a registered API key), and
in practice a large fraction of Commons' UK geotagged photos are themselves
mirrored from Geograph Britain and Ireland — a project purpose-built to
photograph every square kilometre of the UK — so coverage and relevance for
rural/rural-adjacent stargazing sites is good despite going through Commons.

These are general daytime "what does this place look like" photos — not
night-sky photography. There is no free, bulk-sourceable dataset of genuine
night photos per site.

Candidates are filtered to landscape/scenery shots: anything whose title or
Commons categories suggest a specific building, monument, or other object
(a church, a war memorial, a road bridge, ...) is skipped in favour of the
next-nearest candidate that reads as open scenery. If nothing within range
passes, no photo is used — better than showing an irrelevant one.

All Commons content is Creative Commons or public domain; attribution
(photographer + licence) is carried through to the frontend, which is
required for CC BY/BY-SA licensed images.
"""

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "NoHazeStargaze/1.0 (https://no-haze-stargaze.onrender.com; educational project)"}

SEARCH_RADIUS_M = 500       # tight radius: prefer no photo over an irrelevant one
FALLBACK_RADIUS_M = 2000    # widened only if nothing passes close by
CANDIDATES_PER_QUERY = 10   # Commons geosearch gslimit
REQUEST_DELAY = 0.6         # seconds between sites

# Only consider actual photograph-shaped files — skip audio/video/documents
# and vector graphics (usually maps/diagrams/logos, not site photos).
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png)$", re.IGNORECASE)

# Title/category keywords that mean "a specific building or object", not
# open landscape/scenery — the thing we actually want a photo of.
_NOT_LANDSCAPE_RE = re.compile(
    r"church|chapel|cathedral|abbey|priory|"
    r"\bhouse\b|cottage|farmhouse|manor\b|\bhall\b|"
    r"\bbuilding|"
    r"statue|monument|memorial|cenotaph|plaque|sign ?post|notice ?board|"
    r"interior|indoor|"
    r"\bgrave|tomb|cemetery|"
    r"\btower\b|\bspire\b|\bmill\b|windmill|lighthouse|"
    r"\bbridge\b|"
    r"castle|\bruins?\b|\bfort\b|"
    r"\bpub\b|\binn\b|\bhotel\b|\bshop\b|\bschool\b|\bstation\b|"
    r"visitor centre|visitor center|\bcentre\b|\bcenter\b|"
    r"\blogo\b|crest|coat of arms|"
    r"statue|sculpture|\bplinth\b|observatory|planetarium|"
    r"portrait|\bgrave ?stone|"
    # UK churches are almost always named "St X('s)" rather than literally
    # containing the word "church" — geograph.org.uk especially catalogues
    # them this way (e.g. "St. John's, High Legh").
    r"\bst\.?\s?[a-z]+'?s?\b|"
    r"parish|methodist|baptist|cathedral|vicarage|rectory|\bmanse\b|"
    r"\bfont\b|\baltar\b|\bpew\b|\bcross\b|war memorial|"
    r"\bpylon\b|\bmast\b|\btransmitter\b|\bfarm\b|\bbarn\b|"
    r"\bkiosk\b|\bhut\b|\bshelter\b|\btrig point\b|\bpost ?box\b",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html or "").strip()


def _first_href(html: str) -> str | None:
    m = _HREF_RE.search(html or "")
    if not m:
        return None
    href = m.group(1)
    return "https:" + href if href.startswith("//") else href


def _request(params: dict) -> dict | None:
    for attempt in range(2):
        try:
            resp = requests.get(COMMONS_API_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt < 1:
                time.sleep(1.5)
                continue
            logger.debug("Commons API request failed: %s", exc)
            return None
    return None


def _geosearch(lat: float, lon: float, radius_m: int) -> list[dict]:
    data = _request({
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": radius_m,
        "gslimit": CANDIDATES_PER_QUERY,
        "gsnamespace": 6,  # File namespace only
        "format": "json",
    })
    if not data:
        return []
    results = data.get("query", {}).get("geosearch", [])
    return [r for r in results if _IMAGE_EXT_RE.search(r.get("title", ""))]


def _batch_image_info(titles: list[str]) -> dict[str, dict]:
    """One batched imageinfo call for up to CANDIDATES_PER_QUERY titles.
    Returns {title: {photo_thumb_url, photo_page_url, photo_artist,
    photo_artist_url, photo_license, categories}}."""
    if not titles:
        return {}
    data = _request({
        "action": "query",
        "titles": "|".join(titles),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 640,
        "format": "json",
    })
    if not data:
        return {}
    out: dict[str, dict] = {}
    for page in data.get("query", {}).get("pages", {}).values():
        title = page.get("title")
        if not title or "imageinfo" not in page:
            continue
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})
        artist_html = meta.get("Artist", {}).get("value", "")
        out[title] = {
            "photo_thumb_url": info.get("thumburl") or info.get("url"),
            "photo_page_url": info.get("descriptionurl"),
            "photo_artist": _strip_tags(artist_html) or "Unknown",
            "photo_artist_url": _first_href(artist_html),
            "photo_license": meta.get("LicenseShortName", {}).get("value") or None,
            "_categories": meta.get("Categories", {}).get("value", ""),
        }
    return out


def _looks_like_landscape(title: str, categories: str) -> bool:
    return not _NOT_LANDSCAPE_RE.search(f"{title} {categories}")


def _best_candidate(lat: float, lon: float, radius_m: int) -> dict | None:
    results = _geosearch(lat, lon, radius_m)
    if not results:
        return None
    results.sort(key=lambda r: r.get("dist", 1e9))

    time.sleep(0.2)  # be polite between the geosearch and imageinfo calls
    info_by_title = _batch_image_info([r["title"] for r in results])

    for r in results:
        info = info_by_title.get(r["title"])
        if not info or not info["photo_thumb_url"]:
            continue
        if not _looks_like_landscape(r["title"], info["_categories"]):
            continue
        info = {k: v for k, v in info.items() if k != "_categories"}
        info["photo_distance_m"] = round(r.get("dist", 0))
        return info
    return None


def fetch_nearest_photo(lat: float, lon: float) -> dict | None:
    """
    Return the nearest landscape-looking geotagged Commons photo to
    (lat, lon), or None if nothing suitable is found within
    FALLBACK_RADIUS_M. Result includes display URL, attribution, licence,
    and distance in metres.
    """
    info = _best_candidate(lat, lon, SEARCH_RADIUS_M)
    if not info:
        info = _best_candidate(lat, lon, FALLBACK_RADIUS_M)
    if not info:
        return None
    info["photo_fetched_at"] = time.time()
    return info
