param(
    [switch]$SkipNode
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
$envExample = Join-Path $projectRoot ".env.example"
$venvPath = Join-Path $projectRoot ".venv"

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath $envExample -Destination $envFile
    Write-Host "Created $envFile"
}

if (-not (Test-Path -LiteralPath $venvPath)) {
    python -m venv $venvPath
}

& (Join-Path $venvPath "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $venvPath "Scripts\python.exe") -m pip install -r (Join-Path $projectRoot "server\requirements.txt")

if (-not $SkipNode) {
    Push-Location (Join-Path $projectRoot "client")
    try {
        npm install
    }
    finally {
        Pop-Location
    }
}

Write-Host "Setup complete. Add THE_ODDS_API_KEY only to $envFile, then follow README.md."
