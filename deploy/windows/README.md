# Windows Self-Hosted Deployment Guide

Run No-Haze-Stargaze on your own Windows PC, exposed publicly via Cloudflare Tunnel.

**Architecture:**

```
Windows PC
  ├─ Flask app (waitress, port 8000, 127.0.0.1 only)
  ├─ forecast_cache.json  (local, updated every 6-12 h by Task Scheduler)
  ├─ Windows Task Scheduler
  │    ├─ NoHazeAppServer        — starts app at login
  │    └─ NoHazeForecastRefresh  — refreshes forecasts every 6 h
  └─ Cloudflare Tunnel → exposes https://your-domain.com → localhost:8000

User search → reads local forecast cache only (never calls Open-Meteo)
Scheduled refresh → calls Open-Meteo every 6-12 h → updates forecast_cache.json
```

**Data flow:**

```
scripts/refresh_forecasts_local.py
  → Open-Meteo API
  → C:\no-haze-stargaze-data\forecast_cache.json

app.py (user search)
  → reads forecast_cache.json only
  → never calls Open-Meteo
```

---

## Prerequisites

Your PC needs:
1. **Python 3.11+** — download from https://www.python.org/downloads/
   - During install, tick "Add Python to PATH"
2. **Git** — download from https://git-scm.com/download/win
3. **VS Code** (already installed)
4. A **Cloudflare account** with a domain — see [install_cloudflare_tunnel.md](install_cloudflare_tunnel.md)

---

## Part 1 — First-time setup

### 1.1 Clone the repository

Open VS Code → Terminal → New Terminal, then:

```powershell
cd C:\
git clone https://github.com/ngcd04-fa07/no-haze-stargaze.git
cd no-haze-stargaze
git checkout windows-cloudflare
```

### 1.2 Run the setup script

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\deploy\windows\setup.ps1
```

This will:
- Create `C:\no-haze-stargaze-data\` and `logs\`
- Create a Python virtual environment (`.venv\`)
- Install all dependencies (Flask, waitress, requests, etc.)
- Copy `sites_cache.json` to the data directory

---

## Part 2 — First forecast refresh

Run the forecast refresh manually once before starting the app.

> ⚠️ This fetches ~2640 sites from Open-Meteo in batches.
> It takes approximately **30–40 minutes** on first run. Subsequent runs are the same speed.
> Do not interrupt it.

```powershell
.\deploy\windows\refresh_forecasts.ps1
```

Watch the console for progress. When done, check:

```powershell
Get-Item C:\no-haze-stargaze-data\forecast_cache.json | Select-Object Length, LastWriteTime
```

You should see a file of roughly 10–50 MB created in the last few minutes.

---

## Part 3 — Start the app

```powershell
.\deploy\windows\start_app.ps1
```

The app starts on `http://127.0.0.1:8000`. Test it:

```powershell
curl http://127.0.0.1:8000/api/status
```

Expected response:

```json
{
  "deployment_mode": "windows_cloudflare",
  "cache_only_forecasts": true,
  "recommendation_fetches_openmeteo": false,
  "forecast_cache_loaded": true,
  "forecast_sites": 2640,
  "sites_loaded": 2640
}
```

If `forecast_sites` is 0, the forecast cache wasn't loaded correctly — check that the refresh in Part 2 completed and that `C:\no-haze-stargaze-data\forecast_cache.json` exists.

---

## Part 4 — Cloudflare Tunnel

Follow [install_cloudflare_tunnel.md](install_cloudflare_tunnel.md) to:
1. Install `cloudflared`
2. Create and configure a tunnel
3. Install it as a Windows service

After setup, verify:

```powershell
curl https://your-domain.com/api/status
```

---

## Part 5 — Install scheduled tasks (persistent background operation)

```powershell
# Run PowerShell as Administrator
.\deploy\windows\install_task_scheduler.ps1
```

This registers:
- **`NoHazeAppServer`** — starts the app when you log in
- **`NoHazeForecastRefresh`** — refreshes forecasts every 6 hours

Verify tasks registered:

```powershell
Get-ScheduledTask -TaskName "NoHazeForecastRefresh"
Get-ScheduledTask -TaskName "NoHazeAppServer"
```

Run forecast refresh immediately via Task Scheduler:

```powershell
Start-ScheduledTask -TaskName "NoHazeForecastRefresh"
```

---

## Verification checklist

### Step 1 — Local app health

```powershell
curl http://127.0.0.1:8000/api/status
```

Expected:

```json
{
  "deployment_mode": "windows_cloudflare",
  "cache_only_forecasts": true,
  "recommendation_fetches_openmeteo": false
}
```

### Step 2 — Forecast cache loaded

After running `refresh_forecasts.ps1`:

```powershell
curl http://127.0.0.1:8000/api/status
```

Expected:

```json
{
  "forecast_cache_loaded": true,
  "forecast_sites": 2640,
  "forecast_cache_age_seconds": 120
}
```

### Step 3 — Test a recommendation

```powershell
curl -X POST http://127.0.0.1:8000/api/recommend `
  -H "Content-Type: application/json" `
  -d '{"location": "London", "max_distance_km": 150}'
```

You should receive recommendations with `avg_cover` values (not null).

### Step 4 — Public Cloudflare URL

```powershell
curl https://your-domain.com/api/status
```

Expected:

```json
{
  "deployment_mode": "windows_cloudflare",
  "forecast_cache_loaded": true,
  "forecast_sites": 2640,
  "cache_only_forecasts": true,
  "recommendation_fetches_openmeteo": false
}
```

---

## Logs

| File | Contents |
|---|---|
| `C:\no-haze-stargaze-data\logs\forecast_refresh.log` | Forecast refresh output |
| Windows Event Viewer → Task Scheduler | Scheduled task run history |

To tail the forecast refresh log in PowerShell:

```powershell
Get-Content C:\no-haze-stargaze-data\logs\forecast_refresh.log -Tail 50 -Wait
```

---

## Known limitations (beta)

- **PC must stay on and connected** — if the PC sleeps or loses internet, the app goes offline.
- **Disable Windows sleep** — Go to Settings → Power → Sleep → "Never" for both plugged in and on battery.
- **Windows Updates can restart the PC** — the Task Scheduler tasks will restart the app on next login, but there will be downtime.
- **Single process** — no redundancy or failover. Fine for personal/beta use.
- **Forecast refresh takes 30–40 min** — plan the schedule around this (default: every 6 h).

---

## Changing the refresh frequency

To switch to 12-hour refresh, re-run the install script:

```powershell
.\deploy\windows\install_task_scheduler.ps1 -RefreshIntervalHours 12
```

---

## Updating the app

```powershell
# Stop the app first (or let Task Scheduler restart it)
git pull origin windows-cloudflare

# Re-install dependencies if requirements.txt changed
.\.venv\Scripts\pip install -r requirements.txt

# Restart the app
.\deploy\windows\start_app.ps1
```

---

## Note on GitHub Actions

The `.github/workflows/forecast_update.yml` workflow is **legacy / Render-only** and is not used by this Windows deployment.
The local `scripts/refresh_forecasts_local.py` script replaces it entirely.
GitHub Releases are not the production source of truth for this deployment.
