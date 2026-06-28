# Stargazing Recommender

Recommends the best UK stargazing sites for a given location, date, and time window — combining site quality data from **gostargazing.co.uk** with real-time cloud cover forecasts from **Open-Meteo**.

## Features

- Scrapes all ~2,700 stargazing locations from gostargazing.co.uk (name, coordinates, light pollution level, site type)
- Fetches hourly cloud cover forecasts from the free Open-Meteo API (no key required)
- Geocodes UK postcodes via postcodes.io and place names via Nominatim
- Scores and ranks sites on: cloud cover (40%), light pollution (35%), proximity (15%), site type (10%)
- Interactive Leaflet.js map with colour-coded markers
- Shows mini hourly cloud cover chart per site
- Filters by max distance and minimum light pollution quality
- Scraping runs in the background; sites are available after ~100 are found (≈ 30 s)
- Cache is saved to `sites_cache.json` incrementally and refreshed weekly

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

> **First run**: the app scrapes ~2,700 sites in the background. You can start searching after ~100 sites are loaded (≈ 30 seconds). Full data takes ~15 minutes. The cache persists across restarts.

## Usage

1. Enter your **location** (UK postcode or town name, e.g. `M1 1AE` or `Edinburgh`)
2. Select a **date** and **night window** (default 22:00–04:00)
3. Set your **max travel distance** (km)
4. Optionally filter by **minimum light pollution quality**
5. Click **Find Best Sites**

Results appear as a ranked list and colour-coded pins on the map. Click any site for details and a link to its gostargazing.co.uk page.

## Data Sources

| Source | Data | Method |
|---|---|---|
| [gostargazing.co.uk](https://gostargazing.co.uk) | Site name, coordinates, light pollution level, site type | Web scraping (XML sitemap + individual pages) |
| [Open-Meteo](https://open-meteo.com) | Hourly cloud cover forecast | Free API, no key required |
| [postcodes.io](https://postcodes.io) | UK postcode geocoding | Free API |
| [Nominatim (OSM)](https://nominatim.openstreetmap.org) | Place name geocoding | Free API |

## Scoring

| Component | Weight | Description |
|---|---|---|
| Cloud cover | 40% | `100 − avg_cloud_pct` during the night window |
| Light pollution | 35% | Dark site=100, Rural=80, Semi-rural=60, Suburban=40, Urban=20 |
| Distance | 15% | Linear from 100 (at origin) to 0 (at max distance) |
| Site type | 10% | Dark Sky Discovery=100, Recommended=85, Go Stargazing=70 |

## Notes

- The app is for personal, educational use only. Please respect gostargazing.co.uk's servers — the scraper uses a 0.8 s delay between requests.
- Cloud cover data is a forecast; actual conditions may differ.
- Sites without coordinates on the gostargazing.co.uk page are automatically skipped.

## Deploy Frontend On GitHub Pages

GitHub Pages can host the frontend only. The Flask API must be hosted separately (for example on Render, Railway, Fly.io, or your own server).

1. Deploy the Flask backend and note its base URL (example: `https://your-backend.onrender.com`).
2. Build the Pages folder locally:

```bash
mkdir -p docs
cp templates/index.html docs/index.html
```

3. Push to GitHub.
4. In GitHub repo settings, enable Pages with source `Deploy from a branch`, branch `main`, folder `/docs`.
5. Open your Pages site with the API URL query string once:

```text
https://<your-user>.github.io/<your-repo>/?api=https://your-backend.onrender.com
```

The app stores this API URL in local storage after first load, so you do not need to keep the query string every time.

### CORS

This project now sends permissive CORS headers from Flask, so your GitHub Pages origin can call the API.
