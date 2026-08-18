# AISP pre-beta security gate.
# Runs the focused security regression suite and fails fast (non-zero exit)
# on any regression. See docs/AISP_AUTHENTICATED_BETA_SECURITY_GATE.md.

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

$serverDir = Join-Path $repoRoot 'server'
Push-Location $serverDir
try {
    & $python -m pytest `
        tests/test_security_adversarial.py `
        tests/test_security_hardening.py `
        tests/test_api_auth_pipeline.py `
        -q --maxfail=1
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Error "SECURITY GATE FAILED (pytest exit $code)"
    exit $code
}
Write-Host 'SECURITY GATE PASSED' -ForegroundColor Green
exit 0
