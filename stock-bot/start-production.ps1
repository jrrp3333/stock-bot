$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$venvPython = "c:/Users/jrrp3/.vscode/Trade Bot/.venv-1/Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Python venv executable not found at: $venvPython" -ForegroundColor Red
    exit 1
}

$envFile = Join-Path $PSScriptRoot ".env"
$envExample = Join-Path $PSScriptRoot ".env.example"

if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "Created .env from .env.example. Paste API keys and run again." -ForegroundColor Yellow
    exit 1
}

$envContent = Get-Content $envFile -Raw
$required = @("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY")
foreach ($key in $required) {
    $match = [regex]::Match($envContent, "(?m)^$key=(.*)$")
    if (-not $match.Success) {
        Write-Host "Missing $key in .env" -ForegroundColor Red
        exit 1
    }

    $value = $match.Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or $value -like "PASTE_*" -or $value -eq "changeme" -or $value -eq "replace_me") {
        Write-Host "Set a real value for $key in .env" -ForegroundColor Red
        exit 1
    }
}

Write-Host "Starting bot (autonomous) and dashboard..." -ForegroundColor Green
Start-Process -FilePath $venvPython -ArgumentList "bot.py" -WorkingDirectory $PSScriptRoot
Start-Process -FilePath $venvPython -ArgumentList "dashboard.py" -WorkingDirectory $PSScriptRoot

Write-Host "Launched. Dashboard: http://127.0.0.1:5000" -ForegroundColor Green
