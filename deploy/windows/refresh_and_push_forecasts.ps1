# refresh_and_push_forecasts.ps1
#
# Refresh forecast_cache.json, validate it, and push to GitHub main.
# Render auto-deploys from the pushed cache.
#
# Run manually or via Windows Task Scheduler:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<repo>\deploy\windows\refresh_and_push_forecasts.ps1"
#
# Logs to: <repo>\deploy\windows\logs\forecast_refresh_YYYY-MM-DD_HH-mm-ss.log

# Fallback log written to TEMP so Task Scheduler failures are always visible
$FallbackLog = "$env:TEMP\nohaze_refresh_error.log"

# ---------------------------------------------------------------------------
# Resolve repo root from script location
# ---------------------------------------------------------------------------
try {
    $ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
    $RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
} catch {
    $msg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') FATAL: Could not resolve repo root: $_"
    Add-Content -Path $FallbackLog -Value $msg -Encoding UTF8
    Write-Host $msg
    exit 1
}

$LogDir    = Join-Path $RepoRoot "deploy\windows\logs"
$TimeStamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile   = Join-Path $LogDir "forecast_refresh_$TimeStamp.log"

$Python         = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RefreshScript  = Join-Path $RepoRoot "scripts\refresh_forecast_cache.py"
$ValidateScript = Join-Path $RepoRoot "scripts\validate_forecast_cache.py"
$ForecastCache  = Join-Path $RepoRoot "forecast_cache.json"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Write-LogBlank {
    Write-Host ""
    Add-Content -Path $LogFile -Value "" -Encoding UTF8
}

# ---------------------------------------------------------------------------
# Create log dir before setting strict error mode
# ---------------------------------------------------------------------------
try {
    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    Add-Content -Path $LogFile -Value "" -Encoding UTF8
} catch {
    $msg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') FATAL: Could not create log dir '$LogDir': $_"
    Add-Content -Path $FallbackLog -Value $msg -Encoding UTF8
    Write-Host $msg
    exit 1
}

# Ensure git is findable - Task Scheduler uses a stripped PATH
$gitCmdDir = "C:\Program Files\Git\cmd"
$gitBinDir = "C:\Program Files\Git\bin"
if ((Test-Path $gitCmdDir) -and ($env:PATH -notlike "*$gitCmdDir*")) {
    $env:PATH = "$gitCmdDir;$gitBinDir;$env:PATH"
}

Write-Log "=== Forecast refresh + push started ==="
Write-Log "Repo root : $RepoRoot"
Write-Log "Log file  : $LogFile"
Write-LogBlank

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
if (-not (Test-Path $Python)) {
    Write-Log "ERROR: Python venv not found: $Python" "ERROR"
    Write-Log "Run deploy\windows\setup.ps1 first." "ERROR"
    exit 1
}

if (-not (Test-Path $RefreshScript)) {
    Write-Log "ERROR: Refresh script not found: $RefreshScript" "ERROR"
    exit 1
}

# ---------------------------------------------------------------------------
# Step 1: git pull
# ---------------------------------------------------------------------------
Write-Log "[1/5] Pulling latest from origin main..."
Set-Location $RepoRoot

$pullOutput = git pull origin main 2>&1
$pullCode   = $LASTEXITCODE
foreach ($line in $pullOutput) {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$ts] [git] $line"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -Encoding UTF8
}

if ($pullCode -ne 0) {
    Write-Log "WARNING: git pull failed (exit $pullCode). Continuing with local state." "WARN"
}

Write-LogBlank

# ---------------------------------------------------------------------------
# Step 2: Run forecast refresh
# ---------------------------------------------------------------------------
Write-Log "[2/5] Running forecast refresh script..."
Write-Log "      This takes approx 35 min for a full site sweep."
Write-LogBlank

& $Python $RefreshScript
$refreshCode = $LASTEXITCODE

Write-LogBlank

if ($refreshCode -ne 0) {
    Write-Log "ERROR: Forecast refresh failed (exit $refreshCode). Not committing." "ERROR"
    Write-Log "=== RESULT: FAILED (refresh step) ===" "ERROR"
    exit 1
}

Write-Log "[2/5] Forecast refresh succeeded."
Write-LogBlank

# ---------------------------------------------------------------------------
# Step 3: Validate forecast_cache.json
# ---------------------------------------------------------------------------
Write-Log "[3/5] Validating forecast_cache.json..."

if (-not (Test-Path $ForecastCache)) {
    Write-Log "ERROR: forecast_cache.json not found after refresh." "ERROR"
    Write-Log "=== RESULT: FAILED (file missing after refresh) ===" "ERROR"
    exit 1
}

$validateOutput = & $Python $ValidateScript 2>&1
$validateCode   = $LASTEXITCODE
foreach ($line in $validateOutput) {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$ts] [val] $line"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -Encoding UTF8
}

Write-LogBlank

if ($validateCode -ne 0) {
    Write-Log "ERROR: Validation failed (exit $validateCode). Not committing." "ERROR"
    Write-Log "=== RESULT: FAILED (validation) ===" "ERROR"
    exit 1
}

Write-Log "[3/5] Validation passed."
Write-LogBlank

# ---------------------------------------------------------------------------
# Step 4: Check whether forecast_cache.json has changed
# ---------------------------------------------------------------------------
Write-Log "[4/5] Checking for changes in forecast_cache.json..."

$gitStatus = git status --porcelain "forecast_cache.json" 2>&1

$cacheChanged = ($gitStatus -match "forecast_cache.json")

if (-not $cacheChanged) {
    Write-Log "No changes detected in forecast_cache.json - nothing to commit."
    Write-Log "=== RESULT: SUCCESS (no-op, cache unchanged) ==="
    exit 0
}

Write-Log "forecast_cache.json has changed - preparing commit."
Write-LogBlank

# ---------------------------------------------------------------------------
# Step 5: switch to main, commit, push, switch back
# ---------------------------------------------------------------------------
Write-Log "[5/5] Committing and pushing forecast_cache.json to main..."

# Switch to main so the commit lands on the right branch
$checkoutOutput = git checkout main 2>&1
$checkoutCode   = $LASTEXITCODE
foreach ($line in $checkoutOutput) {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$ts] [git] $line"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -Encoding UTF8
}

if ($checkoutCode -ne 0) {
    Write-Log "ERROR: git checkout main failed (exit $checkoutCode)." "ERROR"
    Write-Log "=== RESULT: FAILED (git checkout main) ===" "ERROR"
    exit 1
}

$addOutput = git add "forecast_cache.json" 2>&1
$addCode   = $LASTEXITCODE
foreach ($line in $addOutput) {
    Write-Log "[git] $line"
}

if ($addCode -ne 0) {
    Write-Log "ERROR: git add failed (exit $addCode)." "ERROR"
    Write-Log "=== RESULT: FAILED (git add) ===" "ERROR"
    git checkout - 2>&1 | Out-Null
    exit 1
}

$commitMsg    = "Update forecast cache $(Get-Date -Format 'yyyy-MM-dd HH:mm') UTC"
$commitOutput = git commit -m $commitMsg 2>&1
$commitCode   = $LASTEXITCODE
foreach ($line in $commitOutput) {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$ts] [git] $line"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -Encoding UTF8
}

if ($commitCode -ne 0) {
    Write-Log "ERROR: git commit failed (exit $commitCode)." "ERROR"
    Write-Log "=== RESULT: FAILED (git commit) ===" "ERROR"
    git checkout - 2>&1 | Out-Null
    exit 1
}

$pushOutput = git push origin main 2>&1
$pushCode   = $LASTEXITCODE
foreach ($line in $pushOutput) {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $entry = "[$ts] [git] $line"
    Write-Host $entry
    Add-Content -Path $LogFile -Value $entry -Encoding UTF8
}

# Return to previous branch regardless of push result
git checkout - 2>&1 | Out-Null

if ($pushCode -ne 0) {
    Write-Log "ERROR: git push failed (exit $pushCode)." "ERROR"
    Write-Log "You may need to pull first or check your git credentials." "ERROR"
    Write-Log "=== RESULT: FAILED (git push) ===" "ERROR"
    exit 1
}

Write-LogBlank
Write-Log "=== RESULT: SUCCESS - forecast_cache.json committed and pushed to main ==="
Write-Log "Render will auto-deploy from the new cache within a few minutes."
exit 0
