# Windows Forecast Refresh Scheduler

Every 6 hours this machine fetches fresh forecasts from Open-Meteo, validates
them, commits `forecast_cache.json` to GitHub, and pushes to `main`. Render
detects the push and auto-deploys within ~90 seconds, serving the new cache
to users.

## Files

| File | Purpose |
|------|---------|
| `scripts/refresh_forecast_cache.py` | Fetches all sites from Open-Meteo, validates coverage >= 90%, writes atomically to `forecast_cache.json` |
| `scripts/validate_forecast_cache.py` | Standalone validator — exits 0 on pass, 1 on fail |
| `deploy/windows/refresh_and_push_forecasts.ps1` | Orchestrator: pull → refresh → validate → git add/commit/push |
| `deploy/windows/install_forecast_refresh_task.ps1` | One-time installer for the Windows Scheduled Task |
| `deploy/windows/logs/` | Per-run log files (auto-created) |

## One-time Setup

Run once in an **Administrator** PowerShell window from the repo root:

```powershell
.\deploy\windows\install_forecast_refresh_task.ps1
```

This creates a Scheduled Task named `NoHazeForecastRefresh` that:
- Runs every 6 hours starting immediately
- Uses the repo's `.venv` Python
- Runs as the highest privilege level available
- Does not stop when on battery / does not require mains power
- Skips (does not queue) if a previous run is still active

## Manual Trigger

```powershell
Start-ScheduledTask -TaskName 'NoHazeForecastRefresh'
```

Or run the script directly (takes ~35 min for a full 2640-site sweep):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\deploy\windows\refresh_and_push_forecasts.ps1"
```

## Viewing Logs

```powershell
# List recent log files
Get-ChildItem .\deploy\windows\logs\

# Tail the most recent log
Get-Content (Get-ChildItem .\deploy\windows\logs\ | Sort-Object LastWriteTime -Desc | Select-Object -First 1).FullName -Tail 50
```

Each log file is named `forecast_refresh_YYYY-MM-DD_HH-mm-ss.log` and contains
timestamped `[INFO]` / `[WARN]` / `[ERROR]` lines for every step.

## Checking the Last Run

```powershell
$task = Get-ScheduledTask -TaskName 'NoHazeForecastRefresh'
$info = $task | Get-ScheduledTaskInfo
$info.LastRunTime
$info.LastTaskResult   # 0 = success
```

Task Scheduler exit codes: `0` = success, `1` = failure (check log), `0x41301` = running now.

## Removing the Task

```powershell
Unregister-ScheduledTask -TaskName 'NoHazeForecastRefresh' -Confirm:$false
```

## Architecture Notes

- `refresh_forecast_cache.py` exits **0** if the Open-Meteo rate limit is
  currently active (keeps existing cache, does not push broken data).
- The cache is written via `.tmp` → validate → `os.replace()` so a crash or
  network drop mid-write never corrupts the existing cache.
- If fewer than 90% of sites have forecast data the run fails and nothing is
  committed.
- `git pull` runs before the refresh so the local repo stays in sync with any
  out-of-band pushes (e.g. site scrapes from another machine).
- The commit message is `Update forecast cache YYYY-MM-DD HH:MM UTC`.

## Render Auto-Deploy

After a successful push, Render detects the commit within seconds and
rebuilds the service. The new `forecast_cache.json` is read at startup by
`app.py::_load_forecast_cache()`. Users see the updated "Forecast last
refreshed" timestamp in the status bar within ~90 seconds of the push.
