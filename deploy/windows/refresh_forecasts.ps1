# refresh_forecasts.ps1 — Run a local Open-Meteo forecast refresh
#
# Called by Windows Task Scheduler every 6–12 hours (see install_task_scheduler.ps1).
# Also safe to run manually at any time.
#
# Usage:
#   cd C:\path\to\no-haze-stargaze
#   .\deploy\windows\refresh_forecasts.ps1

param(
    [string]$DataDir = "C:\no-haze-stargaze-data"
)

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$LogDir   = Join-Path $DataDir "logs"
$LogFile  = Join-Path $LogDir "forecast_refresh.log"

Write-Host "=== Forecast Refresh ===" -ForegroundColor Cyan
Write-Host "Repo root : $RepoRoot"
Write-Host "Data dir  : $DataDir"
Write-Host "Log file  : $LogFile"
Write-Host ""

# Ensure log dir exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Activate virtual environment
$activate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Error "Virtual environment not found at $activate. Run setup.ps1 first."
    exit 1
}
. $activate

# Set environment variables
$env:NO_HAZE_DATA_DIR     = $DataDir
$env:CACHE_ONLY_FORECASTS = "true"
$env:DEPLOYMENT_MODE      = "windows_cloudflare"

# Change to repo root
Set-Location $RepoRoot

Write-Host "Running refresh script..." -ForegroundColor Green
python scripts\refresh_forecasts_local.py

$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Warning "Forecast refresh exited with code $exitCode — check $LogFile"
} else {
    Write-Host "Forecast refresh completed successfully." -ForegroundColor Green
}

exit $exitCode
