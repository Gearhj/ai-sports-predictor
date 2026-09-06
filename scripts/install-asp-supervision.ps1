<#
  Installs the tracked backend supervisor for the local 127.0.0.1:8010 runtime.

  RUN THIS FROM A NORMAL, ELEVATED WINDOWS POWERSHELL - not from inside a
  packaged (MSIX) application's terminal.

  Why that matters: a packaged app's writes to %LOCALAPPDATA% are silently
  redirected into its private container
  (...\Packages\<pkg>\LocalCache\Local\...). A copy installed from inside such a
  container is invisible to Task Scheduler, which runs outside it, so the task
  keeps executing the previous wrapper while the installing session sees the new
  one. Verify-After-Install below fails loudly if that happens.

  This script installs a launcher and repoints a scheduled task. It starts no
  provider job and touches no application data.
#>
[CmdletBinding()]
param(
  [string]$RepoRoot  = 'C:\Users\james\ai-sports-predictor',
  [string]$InstallDir = 'C:\Users\james\AppData\Local\asp',
  [string]$TaskName  = 'ASP Backend 8010',
  [switch]$PointTaskAtSupervisor,
  [switch]$Restart
)

$ErrorActionPreference = 'Stop'

$src = Join-Path $RepoRoot 'scripts\start-backend.cmd'
$sup = Join-Path $RepoRoot 'scripts\asp-backend-supervisor.ps1'
foreach ($f in @($src, $sup)) {
  if (-not (Test-Path -LiteralPath $f)) { throw "missing tracked asset: $f" }
}
if (-not (Test-Path -LiteralPath $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }

# Refuse to run from a redirected (packaged-app) view of the install directory.
$probe = Join-Path $InstallDir '.install-redirect-probe'
Set-Content -LiteralPath $probe -Value 'probe' -Encoding ascii
$redirect = (Get-Item -LiteralPath $probe).Target
Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
if ($redirect) {
  throw ("$InstallDir is redirected to $redirect for this session. " +
         'Re-run from a normal elevated Windows PowerShell, not a packaged app terminal.')
}

$dst = Join-Path $InstallDir 'start-backend.cmd'
if (Test-Path -LiteralPath $dst) {
  $backup = "$dst.bak.$(Get-Date -Format 'yyyyMMddTHHmmss')"
  Copy-Item -LiteralPath $dst -Destination $backup -Force
  Write-Host "backed up existing wrapper to $backup"
}
Copy-Item -LiteralPath $src -Destination $dst -Force

$srcHash = (Get-FileHash $src).Hash
$dstHash = (Get-FileHash $dst).Hash
if ($srcHash -ne $dstHash) { throw "install verification failed: $srcHash != $dstHash" }
Write-Host "installed $dst (sha256 $($dstHash.Substring(0,16)))"

if ($PointTaskAtSupervisor) {
  # Removes the cmd layer entirely: the task runs the tracked supervisor directly.
  $action = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument ("-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$sup`" " +
               "-RepoRoot `"$RepoRoot`" -Port 8010 -LogPath `"$InstallDir\backend.log`"") `
    -WorkingDirectory $RepoRoot
  Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
  Write-Host "task '$TaskName' repointed at the supervisor"
}

if ($Restart) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 5
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'port 8010' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 3
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "task '$TaskName' restarted; probe http://127.0.0.1:8010/readyz"
}
