# setup.ps1 — One-time setup for Windows self-hosted deployment
# Run this script once from the repo root in PowerShell (as Administrator if needed).
#
# Usage:
#   cd C:\path\to\no-haze-stargaze
#   .\deploy\windows\setup.ps1

param(
    [string]$DataDir = "C:\no-haze-stargaze-data",
    [string]$RepoRoot = $PSScriptRoot + "\..\..\"
)

$RepoRoot = Resolve-Path $RepoRoot

Write-Host "=== No-Haze-Stargaze Windows Setup ===" -ForegroundColor Cyan
Write-Host "Repo root : $RepoRoot"
Write-Host "Data dir  : $DataDir"
Write-Host ""

# ---- 1. Create data directory ----
Write-Host "[1/5] Creating data directory..." -ForegroundColor Yellow
$dirs = @($DataDir, "$DataDir\logs")
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Write-Host "  Created: $d"
    } else {
        Write-Host "  Exists : $d"
    }
}

# ---- 2. Create Python virtual environment ----
Write-Host ""
Write-Host "[2/5] Setting up Python virtual environment..." -ForegroundColor Yellow
$venvPath = Join-Path $RepoRoot ".venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "  Creating .venv..."
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create venv. Is Python 3.11+ installed? Check: python --version"
        exit 1
    }
    Write-Host "  Created: $venvPath"
} else {
    Write-Host "  Exists : $venvPath"
}

# ---- 3. Install dependencies ----
Write-Host ""
Write-Host "[3/5] Installing Python dependencies..." -ForegroundColor Yellow
$pip = Join-Path $venvPath "Scripts\pip.exe"
& $pip install --upgrade pip --quiet
& $pip install -r (Join-Path $RepoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed."
    exit 1
}
Write-Host "  Dependencies installed."

# ---- 4. Copy sites_cache.json to data dir if present in repo ----
Write-Host ""
Write-Host "[4/5] Copying initial site cache if available..." -ForegroundColor Yellow
$srcSites = Join-Path $RepoRoot "sites_cache.json"
$dstSites = Join-Path $DataDir "sites_cache.json"
if ((Test-Path $srcSites) -and (-not (Test-Path $dstSites))) {
    Copy-Item $srcSites $dstSites
    Write-Host "  Copied sites_cache.json to $DataDir"
} elseif (Test-Path $dstSites) {
    Write-Host "  sites_cache.json already in $DataDir"
} else {
    Write-Host "  No sites_cache.json found — the app will scrape on first run."
}

# ---- 5. Print environment variable summary ----
Write-Host ""
Write-Host "[5/5] Environment variables to set (add to your system or .env):" -ForegroundColor Yellow
Write-Host ""
Write-Host '  $env:NO_HAZE_DATA_DIR     = "' + $DataDir + '"'
Write-Host '  $env:CACHE_ONLY_FORECASTS = "true"'
Write-Host '  $env:DEPLOYMENT_MODE      = "windows_cloudflare"'
Write-Host '  $env:PORT                 = "8000"'
Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Run first forecast refresh  : .\deploy\windows\refresh_forecasts.ps1"
Write-Host "  2. Start the app               : .\deploy\windows\start_app.ps1"
Write-Host "  3. Set up scheduled tasks      : .\deploy\windows\install_task_scheduler.ps1"
Write-Host "  4. Set up Cloudflare Tunnel    : see deploy\windows\install_cloudflare_tunnel.md"
