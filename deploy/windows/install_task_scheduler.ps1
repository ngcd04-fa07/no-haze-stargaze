# install_task_scheduler.ps1 — Register Windows Scheduled Tasks
#
# Creates two tasks:
#   NoHazeForecastRefresh  — runs refresh_and_push_forecasts.ps1 every 8 hours
#   NoHazeAppServer        — runs start_app.ps1 at user logon
#
# Open-Meteo free tier: ~10,000 location-fetches/day. Each full sweep fetches
# 2,640 (one per site). At 8-hour intervals (3 sweeps/day = 7,920 fetches) we
# stay safely under the daily limit.
# WARNING: Do NOT set RefreshIntervalHours below 8 — you will exhaust the
# daily API quota and be rate-limited for the rest of the day.
#
# Run as Administrator (right-click PowerShell → "Run as Administrator").
#
# Usage:
#   cd C:\path\to\no-haze-stargaze
#   .\deploy\windows\install_task_scheduler.ps1

param(
    [int]   $RefreshIntervalHours = 8,        # 3 sweeps/day = 7,920 API calls/day (under 10,000/day limit)
    [string]$User                 = $env:USERNAME
)

$RepoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ScriptsDir = Join-Path $PSScriptRoot ""   # deploy\windows\
$PowerShell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

Write-Host "=== Installing Scheduled Tasks ===" -ForegroundColor Cyan
Write-Host "Repo root     : $RepoRoot"
Write-Host "Refresh every : $RefreshIntervalHours hours"
Write-Host "Running as    : $User"
Write-Host ""

# ---------------------------------------------------------------------------
# Task 1: NoHazeForecastRefresh
# ---------------------------------------------------------------------------
$taskName1   = "NoHazeForecastRefresh"
$scriptPath1 = Join-Path $ScriptsDir "refresh_and_push_forecasts.ps1"
$action1     = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath1`"" `
    -WorkingDirectory $RepoRoot

$trigger1  = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Hours $RefreshIntervalHours) -Once -At (Get-Date)
$settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

Write-Host "[1/2] Registering $taskName1 (every $RefreshIntervalHours hours)..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $taskName1 -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName  $taskName1 `
    -Action    $action1 `
    -Trigger   $trigger1 `
    -Settings  $settings1 `
    -RunLevel  Highest `
    -Force | Out-Null
Write-Host "  Registered: $taskName1"

# ---------------------------------------------------------------------------
# Task 2: NoHazeAppServer
# ---------------------------------------------------------------------------
$taskName2   = "NoHazeAppServer"
$scriptPath2 = Join-Path $ScriptsDir "start_app.ps1"
$action2     = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath2`"" `
    -WorkingDirectory $RepoRoot

$trigger2  = New-ScheduledTaskTrigger -AtLogOn -User $User
$settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

Write-Host ""
Write-Host "[2/2] Registering $taskName2 (at logon)..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $taskName2 -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName  $taskName2 `
    -Action    $action2 `
    -Trigger   $trigger2 `
    -Settings  $settings2 `
    -RunLevel  Highest `
    -Force | Out-Null
Write-Host "  Registered: $taskName2"

Write-Host ""
Write-Host "=== Scheduled tasks installed ===" -ForegroundColor Green
Write-Host ""
Write-Host "To verify:"
Write-Host "  Get-ScheduledTask -TaskName 'NoHazeForecastRefresh'"
Write-Host "  Get-ScheduledTask -TaskName 'NoHazeAppServer'"
Write-Host ""
Write-Host "To run the forecast refresh immediately:"
Write-Host "  Start-ScheduledTask -TaskName 'NoHazeForecastRefresh'"
