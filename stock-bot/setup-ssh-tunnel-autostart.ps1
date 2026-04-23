param(
    [string]$DropletIp = "157.245.136.81",
    [string]$TaskName = "TradeBot-SSH-Tunnel-5500"
)

$ErrorActionPreference = "Stop"

$sshExe = "C:\Windows\System32\OpenSSH\ssh.exe"
if (-not (Test-Path $sshExe)) {
    throw "ssh.exe not found at $sshExe"
}

Write-Host "Cleaning old TradeBot SSH tunnel tasks..." -ForegroundColor Yellow
Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like "TradeBot-SSH-Tunnel-*" } |
    ForEach-Object {
        Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
        Write-Host "Removed: $($_.TaskName)" -ForegroundColor DarkYellow
    }

Write-Host "Registering new task: $TaskName" -ForegroundColor Green
$actionArgs = "-N -o ExitOnForwardFailure=yes -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -L 5500:127.0.0.1:5500 root@$DropletIp"
$action = New-ScheduledTaskAction -Execute $sshExe -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)
$fullUser = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $fullUser -LogonType Interactive -RunLevel Limited

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal | Out-Null

Write-Host "Created task: $TaskName" -ForegroundColor Green
Write-Host "Tunnel target: root@$DropletIp (127.0.0.1:5500 -> 127.0.0.1:5500)" -ForegroundColor Green

Write-Host "`nCurrent TradeBot tasks:" -ForegroundColor Cyan
Get-ScheduledTask |
    Where-Object { $_.TaskName -like "TradeBot-*" } |
    Select-Object TaskName, State |
    Sort-Object TaskName |
    Format-Table -AutoSize

Write-Host "`nIf this is your first SSH connection from this account, run once manually to trust host key:" -ForegroundColor Yellow
Write-Host "ssh root@$DropletIp" -ForegroundColor Yellow
