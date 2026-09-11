<#
.SYNOPSIS
  Stop one positively identified duplicate AISP scheduler. Run as Administrator.

.DESCRIPTION
  A second backend process tree survived a previous run of the "ASP Backend
  8010" task. It no longer serves port 8010, so the supervisor's port-owner
  guard cannot see it, but it still runs a full APScheduler against the same
  database -- roughly ninety extra job runs an hour, all failing, and in the
  last measured day 55 SportsGameOdds requests spending 678 objects of a
  bounded 2,500-object allowance under superseded code.

  IDENTIFICATION (all four must still hold at execution, or this exits without
  touching anything):

    1. The tree is python.exe processes created at 2026-09-09 21:53:03.
    2. Neither of them serves port 8010.
    3. Something else DOES serve port 8010 and answers /readyz.
    4. The live listener descends from the Task Scheduler service, not from
       the tree being stopped.

  The evidence that identified it, for the record:

    * Scheduler cadence phase. Five-minute jobs fire in two interleaved
      series, one all SUCCEEDED and one all FAILED with [Errno 22]. The
      failing series lands exactly on 21:53:03 + k*5min; the healthy one
      lands exactly on the live backend's own start + k*5min.
    * Postgres. Ten sessions carry backend_start 2026-09-09 21:53, matching
      the process creation time.
    * Volume. Job runs per hour went from ~105 to ~190 at 22:00 on 2026-09-09
      while successes stayed flat at ~95-100. The duplicate added failures; it
      did not take runs away from the healthy owner.
    * Code age. Jobs that exist only in code written after 2026-09-09 run
      exclusively on the healthy cadence and have never failed.

.NOTES
  This stops ONLY those two PIDs and their cmd.exe wrapper. It does not touch
  PostgreSQL, the live backend, the V18 publisher, or the side-stack workers
  running from bash.exe. If the machine has been rebooted or the processes
  have changed since this was written, every guard fails closed and nothing is
  stopped -- re-run the identification rather than editing the PIDs here.

  Safe to run twice. If the tree is already gone it reports that and exits 0.
#>
[CmdletBinding()]
param(
    [int[]] $SuspectPids   = @(72392, 35996),
    [int]   $WrapperPid    = 70744,
    [datetime] $ExpectedStart = '2026-09-09 21:53:03',
    [int]   $Port          = 8010,
    [switch] $WhatIfOnly
)

$ErrorActionPreference = 'Stop'

function Fail([string] $why) {
    Write-Host "REFUSED: $why" -ForegroundColor Red
    Write-Host "Nothing was stopped. Re-run the identification before forcing anything."
    exit 2
}

# -- 0. elevation ------------------------------------------------------------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail 'this window is not elevated. The duplicate runs elevated, so a medium-integrity shell gets Access Denied. Re-open PowerShell with "Run as administrator".'
}

# -- 1. the live backend must exist, and must not be the suspect -------------
$live = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
         Select-Object -First 1).OwningProcess
if (-not $live) {
    Fail "nothing is listening on port $Port. Either the backend is down (start the task first) or the situation has changed."
}
if ($SuspectPids -contains $live -or $WrapperPid -eq $live) {
    Fail "the process this script would stop (pid $live) is currently SERVING port $Port. That inverts the whole diagnosis."
}

try {
    $ready = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/readyz" -UseBasicParsing -TimeoutSec 5).Content
} catch {
    Fail "pid $live holds port $Port but does not answer /readyz. Do not remove the other tree while the live one is unhealthy -- fix the live backend first."
}
Write-Host "live backend: pid $live  /readyz -> $ready"

# -- 2. the live backend must descend from the Task Scheduler service --------
function Get-Ancestry([int] $start) {
    $chain = @(); $cur = $start
    for ($i = 0; $i -lt 10 -and $cur; $i++) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $p) { break }
        $chain += [pscustomobject]@{ Id = [int]$p.ProcessId; Name = $p.Name }
        $cur = [int]$p.ParentProcessId
    }
    return $chain
}
$svc = (Get-CimInstance Win32_Service -Filter "Name='Schedule'").ProcessId
$liveChain = Get-Ancestry $live
if (-not ($liveChain.Id -contains $svc)) {
    Fail "the live listener (pid $live) does not descend from the Task Scheduler service (pid $svc). Chain: $(($liveChain | ForEach-Object { "$($_.Id):$($_.Name)" }) -join ' < '). Identify the real owner before removing anything."
}
Write-Host "live ancestry ok: $(($liveChain | ForEach-Object { "$($_.Id):$($_.Name)" }) -join ' < ')"

# -- 3. each suspect must still be exactly what was identified ---------------
$targets = @()
foreach ($target in $SuspectPids) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$target" -ErrorAction SilentlyContinue
    if (-not $p) { Write-Host "pid $target is already gone."; continue }
    if ($p.Name -ne 'python.exe') {
        Fail "pid $target is '$($p.Name)', not python.exe. That PID has been reused by something else."
    }
    if ([math]::Abs(($p.CreationDate - $ExpectedStart).TotalSeconds) -gt 5) {
        Fail "pid $target started $($p.CreationDate), not $ExpectedStart. That PID has been reused; the duplicate may be gone already."
    }
    $chain = Get-Ancestry $target
    if ($chain.Id -contains $svc) {
        Fail "pid $target descends from the Task Scheduler service. It is a supervised process, not the survivor."
    }
    $targets += $p
}
if (-not $targets) { Write-Host "Nothing to stop; the duplicate tree is already gone." -ForegroundColor Green; exit 0 }

foreach ($t in $targets) {
    Write-Host ("  target pid {0}  created {1}  ancestry {2}" -f $t.ProcessId, $t.CreationDate,
        ((Get-Ancestry $t.ProcessId | ForEach-Object { "$($_.Id):$($_.Name)" }) -join ' < '))
}
if ($WhatIfOnly) { Write-Host "-WhatIfOnly set; stopping nothing." -ForegroundColor Yellow; exit 0 }

# -- 4. stop child first, then parent, then the wrapper ----------------------
foreach ($target in @($SuspectPids + $WrapperPid)) {
    $p = Get-Process -Id $target -ErrorAction SilentlyContinue
    if (-not $p) { Write-Host "  $target already gone"; continue }
    if ($target -eq $WrapperPid -and $p.ProcessName -ne 'cmd') {
        Write-Host "  skipping wrapper $target : it is '$($p.ProcessName)', not cmd" -ForegroundColor Yellow
        continue
    }
    try { Stop-Process -Id $target -Force -ErrorAction Stop; Write-Host "  stopped $target" }
    catch { Write-Host "  could NOT stop $target : $($_.Exception.Message)" -ForegroundColor Yellow }
}

# -- 5. the live backend must have survived ----------------------------------
Start-Sleep -Seconds 4
$stillLive = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
              Select-Object -First 1).OwningProcess
if ($stillLive -ne $live) {
    Write-Host "WARNING: port $Port is now held by pid $stillLive (was $live)." -ForegroundColor Yellow
    Write-Host 'Restore with:  Start-ScheduledTask -TaskName "ASP Backend 8010"'
    exit 1
}
try {
    $ready = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/readyz" -UseBasicParsing -TimeoutSec 10).Content
    Write-Host "live backend still healthy: pid $live  /readyz -> $ready" -ForegroundColor Green
} catch {
    Write-Host "WARNING: pid $live no longer answers /readyz." -ForegroundColor Yellow
    Write-Host 'Restore with:  Start-ScheduledTask -TaskName "ASP Backend 8010"'
    exit 1
}

Write-Host ""
Write-Host "Confirm over the next ten minutes: [Errno 22] job runs should stop" -ForegroundColor Green
Write-Host "entirely, leaving one five-minute cadence instead of two interleaved ones." -ForegroundColor Green
exit 0
