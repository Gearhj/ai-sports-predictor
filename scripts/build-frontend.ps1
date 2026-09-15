<#
  AI Sports Predictor - build the production frontend served by "ASP Backend 8010".

  After a Windows restart the backend recovers under Task Scheduler and serves
  client\dist at http://127.0.0.1:8010/. This script produces that build. It
  starts no server and registers nothing: the backend is the single owner.

  The API base is built as the same-origin "/api/v1", set in this process only,
  so no .env file is edited. Only VITE_-prefixed variables reach the bundle, and
  VITE_API_URL is the only one.

  Usage (from any directory):
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\james\ai-sports-predictor\scripts\build-frontend.ps1
#>
[CmdletBinding()]
param([string]$RepoRoot = 'C:\Users\james\ai-sports-predictor')

$ErrorActionPreference = 'Stop'
$client = Join-Path $RepoRoot 'client'
$dist = Join-Path $client 'dist'

$env:VITE_API_URL = '/api/v1'
Push-Location $client
try {
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw "frontend build failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
  Remove-Item Env:VITE_API_URL -ErrorAction SilentlyContinue
}

$commit = (& git -C $RepoRoot rev-parse HEAD).Trim()
$dirty = [bool](& git -C $RepoRoot status --porcelain -- client)
$identity = [ordered]@{
  schema_version = 'AISP_FRONTEND_BUILD_IDENTITY_V1'
  commit         = $commit
  client_tree_dirty_at_build = $dirty
  built_at_utc   = [DateTime]::UtcNow.ToString('o')
  api_base       = '/api/v1'
  served_by      = 'ASP Backend 8010 (http://127.0.0.1:8010/)'
}
$json = $identity | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $dist 'build-identity.json'), $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "built $dist at commit $commit (client tree dirty: $dirty)"
