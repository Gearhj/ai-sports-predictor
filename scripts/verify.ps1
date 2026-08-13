$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$aspTestEnvironment = @{
    APP_ENV = "test"
    DATABASE_URL = "sqlite://"
    # A whitespace override remains present in the process environment and is
    # normalized to an empty key by Settings, preventing fallback to `.env`.
    THE_ODDS_API_KEY = " "
    ODDS_POLLING_ENABLED = "false"
    OPEN_METEO_ENABLED = "false"
    STATCAST_ENABLED = "false"
    SCHEDULER_ENABLED = "false"
    OFFICIAL_POSITIONS_ENABLED = "false"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing ASP virtual environment. Run scripts\setup.ps1 first."
}

& git -C $projectRoot check-ignore --quiet -- .env
if ($LASTEXITCODE -ne 0) {
    throw ".env must remain excluded from Git before verification can continue."
}

Push-Location (Join-Path $projectRoot "server")
try {
    foreach ($entry in $aspTestEnvironment.GetEnumerator()) {
        Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value
    }
    $pytestArtifacts = Join-Path $projectRoot ".test-artifacts"
    New-Item -ItemType Directory -Force -Path $pytestArtifacts | Out-Null
    $pytestTemp = Join-Path $pytestArtifacts ("backend-" + [guid]::NewGuid().ToString("N"))
    & $python -m pytest --basetemp=$pytestTemp
    if ($LASTEXITCODE -ne 0) {
        throw "Backend test suite failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Push-Location (Join-Path $projectRoot "client")
try {
    npm test
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend test suite failed with exit code $LASTEXITCODE."
    }
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend production build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Write-Host "ASP backend tests, frontend tests, and production client build passed."
