# install_forecast_refresh_task.ps1
#
# Creates (or replaces) the Windows Scheduled Task "NoHazeForecastRefresh".
# Runs refresh_and_push_forecasts.ps1 every 6 hours.
#
# Run as Administrator:
#   Right-click PowerShell -> "Run as Administrator"
#   cd C:\no-haze-stargaze
#   .\deploy\windows\install_forecast_refresh_task.ps1
#
# To change the interval:
#   .\deploy\windows\install_forecast_refresh_task.ps1 -IntervalHours 12

param(
    [int]$IntervalHours = 6
)

$TaskName  = "NoHazeForecastRefresh"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

$PushScript  = Join-Path $RepoRoot "deploy\windows\refresh_and_push_forecasts.ps1"
$PSExe       = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$Argument    = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PushScript`""

Write-Host ""
Write-Host "=== Installing NoHazeForecastRefresh Scheduled Task ===" -ForegroundColor Cyan
Write-Host "Repo root   : $RepoRoot"
Write-Host "Script      : $PushScript"
Write-Host "Interval    : every $IntervalHours hours"
Write-Host ""

# Verify the script exists before registering
if (-not (Test-Path $PushScript)) {
    Write-Host "ERROR: Script not found: $PushScript" -ForegroundColor Red
    Write-Host "Make sure you are running from the correct repo root." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Task components
# ---------------------------------------------------------------------------

$action = New-ScheduledTaskAction `
    -Execute $PSExe `
    -Argument $Argument `
    -WorkingDirectory $RepoRoot

# Repeating trigger: start now, repeat every N hours indefinitely
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit       (New-TimeSpan -Hours ($IntervalHours - 1)) `
    -StartWhenAvailable `
    -MultipleInstances        IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable

# ---------------------------------------------------------------------------
# Register (replace any existing task with the same name)
# ---------------------------------------------------------------------------
Write-Host "Removing existing task (if any)..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$principal = New-ScheduledTaskPrincipal `
    -UserId    "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel  Highest

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
Write-Host "Task name    : $TaskName"
Write-Host "Runs every   : $IntervalHours hours"
Write-Host "Next run     : $(($registered.Triggers | Select-Object -First 1).StartBoundary)"
Write-Host ""
Write-Host "--- Useful commands ---"
Write-Host ""
Write-Host "Verify task:"
Write-Host "  Get-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Trigger manually (runs refresh + push now):"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Check last run result:"
Write-Host "  (Get-ScheduledTask -TaskName '$TaskName').LastRunTime"
Write-Host "  (Get-ScheduledTask -TaskName '$TaskName').LastTaskResult"
Write-Host ""
Write-Host "View logs:"
Write-Host "  Get-ChildItem '$RepoRoot\deploy\windows\logs\'"
Write-Host "  Get-Content   '$RepoRoot\deploy\windows\logs\<latest>.log' -Tail 50"
Write-Host ""
Write-Host "Remove task:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
