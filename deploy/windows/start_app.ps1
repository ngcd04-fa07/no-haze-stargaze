# start_app.ps1 — Start the No-Haze-Stargaze web app on Windows
#
# Uses waitress (Windows-compatible WSGI server).
# Binds to 127.0.0.1:8000 only — Cloudflare Tunnel exposes it publicly.
#
# Usage:
#   cd C:\path\to\no-haze-stargaze
#   .\deploy\windows\start_app.ps1

param(
    [string]$DataDir  = "C:\no-haze-stargaze-data",
    [string]$Host     = "127.0.0.1",
    [int]   $Port     = 8000,
    [int]   $Threads  = 4
)

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "=== Starting No-Haze-Stargaze ===" -ForegroundColor Cyan
Write-Host "Repo root : $RepoRoot"
Write-Host "Data dir  : $DataDir"
Write-Host "Listening : http://$($Host):$Port"
Write-Host ""

# Activate virtual environment
$activate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Error "Virtual environment not found. Run setup.ps1 first."
    exit 1
}
. $activate

# Set environment variables
$env:NO_HAZE_DATA_DIR     = $DataDir
$env:CACHE_ONLY_FORECASTS = "true"
$env:DEPLOYMENT_MODE      = "windows_cloudflare"
$env:PORT                 = "$Port"

# Change to repo root so relative imports work
Set-Location $RepoRoot

# Start waitress
Write-Host "Starting waitress on $($Host):$Port ..." -ForegroundColor Green
waitress-serve --host=$Host --port=$Port --threads=$Threads app:app
