# No-Haze Stargaze

A web app that recommends the best UK stargazing sites for a given location, date, and time window — combining a database of 2,600+ known dark-sky sites with real-time cloud cover forecasts, local light-pollution detection, moon phase, and site amenities.

**Live app:** [no-haze-stargaze.onrender.com](https://no-haze-stargaze.onrender.com)

## Features

**Search & discovery**
- Search by location (UK postcode or place name) or search directly by site name with autocomplete
- "Include all of UK" mode to search nationwide instead of a fixed radius
- Adjustable search radius (10–200 miles) with a distance slider
- Custom date picker and night-window (start/end hour) pickers, constrained to sunset–sunrise and to each other so Start and End always stay at least an hour apart
- Filter results by minimum light-pollution quality, and by confirmed on-site parking/toilets
- Sort by best overall conditions or by closest distance
- Free-text filter box to narrow the current results list by name or area

**Scoring & ranking**
- Every site is scored 0–100 from cloud cover, light pollution, and distance, combined into a ranked top-25 list
- Score weights are user-adjustable via a "Weights" panel (sliders that always sum to 100%), so you can prioritize, say, clear skies over travel distance
- A separate "Include all of UK" weighting drops distance entirely, since it's not meaningful at nationwide scale

**Light pollution, now including local street lighting**
- Each site carries a regional light-pollution rating (Dark site / Rural / Semi-rural / Suburban / Urban)
- That rating is blended with a local streetlight check: sites near OpenStreetMap-tagged street lamps or lit roads get a "Local light pollution expected" flag and a reduced score, on top of their regional rating — a site rated "Rural" right next to a lit car park scores worse than one that's genuinely dark
- The map card shows the distance to the nearest detected light source

**Restricted-access detection**
- Sites that gostargazing.co.uk itself marks as privately owned / requiring prior arrangement are automatically detected and excluded from the default results
- A "Show Restricted Access sites" checkbox merges them back into the ranked list (at their correct scored position) if you still want to see them, e.g. to arrange a visit
- A second, lower-confidence "likely restricted" tier catches looser wording (mostly observatories and astronomical societies) — these stay visible in the normal results with a warning chip rather than being filtered

**Moon phase**
- Current moon phase, illumination percentage, and a stargazing-quality descriptor (excellent → very poor) are shown for the selected night

**Amenities**
- Parking and toilet availability, sourced two ways: a yes/no signal from each site's own gostargazing.co.uk listing (used for the parking/toilet filters), and nearest-distance lookups against OpenStreetMap for on-map display
- A "Directions" link and a link through to the site's full gostargazing.co.uk page

**Map & UI**
- Interactive Leaflet.js map with colour-coded, score-ranked markers alongside the results list
- Light and dark theme toggle
- Fully responsive mobile layout, including touch-friendly custom dropdowns, tap-to-open tooltips, and a compact results header

## How scoring works

Each site gets a 0–100 score from a weighted blend of:

| Component | Default weight | What it measures |
|---|---|---|
| Cloud cover | 38% | Forecast cloud cover during your chosen night window (lower is better) |
| Light pollution | 37% | 75% regional light-pollution rating + 25% local street-lighting proximity |
| Distance | 25% | How far the site is from your search location |

When "Include all of UK" is active, distance is dropped and the remaining two are reweighted to roughly 51% cloud / 49% pollution. All weights are adjustable from the Weights panel in the app.

Moon illumination is calculated and displayed for context but isn't currently part of the score itself.

## Data sources

| Source | Data | How it's used |
|---|---|---|
| [gostargazing.co.uk](https://gostargazing.co.uk) | Site name, address, coordinates, regional light-pollution rating, site type, on-site parking/toilet listings, and access-restriction notices | The core site database and the parking/toilet filter checkboxes |
| [Open-Meteo](https://open-meteo.com) | Hourly cloud cover forecast | Cloud cover scoring and the per-hour forecast chart on each site card |
| [OpenStreetMap](https://www.openstreetmap.org) (via the Overpass API) | Street lamps, lit roads, parking areas, and toilets near each site | Local light-pollution detection and on-map amenity distances |
| [postcodes.io](https://postcodes.io) | UK postcode geocoding | Turning a postcode search into a map location |
| [Nominatim](https://nominatim.openstreetmap.org) (OpenStreetMap) | Place name geocoding (Great Britain & Ireland) | Turning a town/city search into a map location |

Lunar phase and illumination are calculated locally (no external source) from the date using a standard synodic-period formula.

## Tech stack

- **Backend:** Python, Flask, served with Gunicorn
- **Frontend:** Vanilla HTML/CSS/JavaScript (no framework), Leaflet.js for the map
- **Deployment:** [Render](https://render.com) (auto-deploys from `main`)

## Setup

```bash
# 1. Create a virtual environment (optional but recommended)
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

Then open **http://localhost:5003** in your browser.

## Usage

1. Enter your **location** (UK postcode or town name, e.g. `M1 1AE` or `Edinburgh`) — or switch to **By Site Name** to look up a specific site directly
2. Select a **date** and **night window**
3. Set your **max travel distance**, or check **Include all of UK** to search nationwide
4. Optionally filter by **minimum light-pollution quality**, **parking**, or **toilets**
5. Click **Find Best Sites**

Results appear as a ranked list and colour-coded pins on the map. Click any site for its full breakdown, or use the **Weights** button to customise how much cloud cover, light pollution, and distance each count toward the score.

## Notes

- The app is for personal, educational use only.
- Cloud cover is a forecast; actual conditions may differ, and forecasts further into the future are less reliable (the app labels each result's forecast confidence accordingly).
- Sites without usable coordinates on their gostargazing.co.uk page are excluded from the database.
- Amenity, street-lighting, and restricted-access data reflect what's currently available from OpenStreetMap and gostargazing.co.uk and may be incomplete for some sites.
