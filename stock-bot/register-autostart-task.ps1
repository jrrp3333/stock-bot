param(
    [string]$TaskName = "TradeBot-Autostart",
    [ValidateSet("Logon", "Startup")]
    [string]$TriggerMode = "Logon",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $scriptDir "start-production.ps1"

if (-not (Test-Path $startScript)) {
    Write-Host "Missing start script: $startScript" -ForegroundColor Red
    exit 1
}

# Build a hidden PowerShell launch action.
$escapedStartScript = $startScript.Replace('"', '""')
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$escapedStartScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs

if ($TriggerMode -eq "Startup") {
    $trigger = New-ScheduledTaskTrigger -AtStartup
}
else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$fullUser = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal `
    -UserId $fullUser `
    -LogonType Interactive `
    -RunLevel Limited

if ($WhatIf) {
    Write-Host "[WhatIf] Would register task '$TaskName' with trigger '$TriggerMode'." -ForegroundColor Yellow
    Write-Host "         Action: powershell.exe $actionArgs" -ForegroundColor Yellow
    exit 0
}

# Replace existing task if present.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal | Out-Null
}
catch {
    # Fallback path: schtasks often works in non-elevated user contexts.
    $scheduleType = if ($TriggerMode -eq "Startup") { "ONSTART" } else { "ONLOGON" }
    $launcherDir = Join-Path $env:LOCALAPPDATA "TradeBot"
    if (-not (Test-Path $launcherDir)) {
        New-Item -ItemType Directory -Path $launcherDir | Out-Null
    }

    $launcherScript = Join-Path $launcherDir "start-production-launcher.ps1"
    @"
Set-Location '$scriptDir'
& '$startScript'
"@ | Set-Content -Path $launcherScript -Encoding UTF8

    $taskRun = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $launcherScript"
    $createCmd = @(
        "/Create",
        "/SC", $scheduleType,
        "/TN", $TaskName,
        "/TR", $taskRun,
        "/F"
    )
    if ($TriggerMode -eq "Logon") {
        $createCmd += @("/RL", "LIMITED")
    }

    $null = & schtasks.exe @createCmd
    if ($LASTEXITCODE -ne 0) {
        throw "Fallback registration via schtasks.exe failed with code $LASTEXITCODE"
    }
}

Write-Host "Registered scheduled task '$TaskName' ($TriggerMode)." -ForegroundColor Green
Write-Host "Task will run: $startScript" -ForegroundColor Green
