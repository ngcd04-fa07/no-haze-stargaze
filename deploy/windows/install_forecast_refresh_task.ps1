# install_forecast_refresh_task.ps1
#
# Creates (or replaces) the Windows Scheduled Task "NoHazeForecastRefresh".
# Runs refresh_and_push_forecasts.ps1 every N hours indefinitely.
#
# Run as Administrator:
#   Right-click PowerShell -> "Run as Administrator"
#   .\deploy\windows\install_forecast_refresh_task.ps1
#
# To change the interval:
#   .\deploy\windows\install_forecast_refresh_task.ps1 -IntervalHours 6

param(
    [int]$IntervalHours = 3
)

$TaskName  = "NoHazeForecastRefresh"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

$PushScript = Join-Path $RepoRoot "deploy\windows\refresh_and_push_forecasts.ps1"
$PSExe      = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$Argument   = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PushScript`""

Write-Host ""
Write-Host "=== Installing NoHazeForecastRefresh Scheduled Task ===" -ForegroundColor Cyan
Write-Host "Repo root   : $RepoRoot"
Write-Host "Script      : $PushScript"
Write-Host "Interval    : every $IntervalHours hours"
Write-Host ""

if (-not (Test-Path $PushScript)) {
    Write-Host "ERROR: Script not found: $PushScript" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Task components
# ---------------------------------------------------------------------------

$action = New-ScheduledTaskAction `
    -Execute          $PSExe `
    -Argument         $Argument `
    -WorkingDirectory $RepoRoot

# RepetitionDuration must be MaxValue for the task to repeat indefinitely.
# Without it, Windows stops repeating after a short default window.
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At                 (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# ExecutionTimeLimit: allow up to (IntervalHours - 1) hours so a slow sweep
# never blocks the next scheduled run.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit     (New-TimeSpan -Hours ($IntervalHours - 1)) `
    -StartWhenAvailable `
    -MultipleInstances      IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId    "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel  Highest

# ---------------------------------------------------------------------------
# Register (replaces any existing task with the same name)
# ---------------------------------------------------------------------------
Write-Host "Removing existing task (if any)..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "Registering task for user: $env:USERDOMAIN\$env:USERNAME..." -ForegroundColor Yellow
Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

$registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $registered) {
    Write-Host "ERROR: Task registration failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Task installed successfully ===" -ForegroundColor Green
Write-Host ""
Write-Host "Task name  : $TaskName"
Write-Host "Runs every : $IntervalHours hours (indefinitely)"
Write-Host ""
Write-Host "--- Useful commands ---"
Write-Host ""
Write-Host "Trigger manually:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Check last run:"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName' | Select LastRunTime, LastTaskResult"
Write-Host ""
Write-Host "View logs:"
Write-Host "  Get-ChildItem '$RepoRoot\deploy\windows\logs\'"
Write-Host ""
Write-Host "Remove task:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
