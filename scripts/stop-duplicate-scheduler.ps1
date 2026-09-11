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
  This stops ONLY those two PIDs and, separately proven, their cmd.exe wrapper.
  It does not touch PostgreSQL, the live backend, the V18 publisher, or the
  side-stack workers running from bash.exe. If the machine has been rebooted or
  the processes have changed since this was written, every guard fails closed
  and nothing is stopped -- re-run the identification rather than editing the
  PIDs here.

  The wrapper is held to a higher bar than the suspects, because a cmd.exe PID
  is the most reusable thing on the machine and killing the wrong one is how a
  cleanup script becomes the outage. It is stopped only when it appears in the
  validated ancestry of a suspect that already passed every check, AND its
  image is cmd.exe, AND its creation time matches, AND its command line can
  actually be read and shows it launching this backend. An unreadable command
  line is a refusal, not a shrug: proving nothing is not the same as proving
  it is safe.

  After stopping, both suspects must be gone and must stay gone across a short
  watch, and no replacement may appear beneath the wrapper. A survivor or a
  restart exits nonzero, because "I sent the signal" is not the outcome anyone
  needs; "the duplicate is no longer running" is.

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
#: Every process id appearing in a VALIDATED suspect's ancestry. The wrapper is
#: allowed to be stopped only if it is in here.
$suspectAncestorIds = @()
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
    $suspectAncestorIds += $chain.Id
    $targets += $p
}
if (-not $targets) { Write-Host "Nothing to stop; the duplicate tree is already gone." -ForegroundColor Green; exit 0 }

foreach ($t in $targets) {
    Write-Host ("  target pid {0}  created {1}  ancestry {2}" -f $t.ProcessId, $t.CreationDate,
        ((Get-Ancestry $t.ProcessId | ForEach-Object { "$($_.Id):$($_.Name)" }) -join ' < '))
}
# -- 4. the wrapper must PROVE it belongs to this tree -----------------------
# Four independent facts, every one required. A cmd.exe pid is the most
# reusable number on the machine, so "it was the wrapper two hours ago" is not
# evidence about what it is now.
$wrapperProc = Get-CimInstance Win32_Process -Filter "ProcessId=$WrapperPid" -ErrorAction SilentlyContinue
$stopWrapper = $false
if (-not $wrapperProc) {
    Write-Host "  wrapper $WrapperPid : already gone, nothing to prove."
} elseif ($suspectAncestorIds -notcontains $WrapperPid) {
    Write-Host ("  wrapper {0} : REFUSED -- not in the validated ancestry of any suspect (ancestry seen: {1})." -f
        $WrapperPid, (($suspectAncestorIds | Sort-Object -Unique) -join ', ')) -ForegroundColor Yellow
} elseif ($wrapperProc.Name -ne 'cmd.exe') {
    Write-Host "  wrapper $WrapperPid : REFUSED -- image is '$($wrapperProc.Name)', not cmd.exe." -ForegroundColor Yellow
} elseif ([math]::Abs(($wrapperProc.CreationDate - $ExpectedStart).TotalSeconds) -gt 5) {
    Write-Host "  wrapper $WrapperPid : REFUSED -- created $($wrapperProc.CreationDate), not $ExpectedStart." -ForegroundColor Yellow
} elseif ([string]::IsNullOrWhiteSpace($wrapperProc.CommandLine)) {
    Write-Host "  wrapper $WrapperPid : REFUSED -- its command line cannot be read, so nothing about it is proven." -ForegroundColor Yellow
} elseif ($wrapperProc.CommandLine -notmatch 'uvicorn' -or
          $wrapperProc.CommandLine -notmatch 'app\.main:app' -or
          $wrapperProc.CommandLine -notmatch [regex]::Escape("--port $Port")) {
    Write-Host "  wrapper $WrapperPid : REFUSED -- command line does not launch this backend:" -ForegroundColor Yellow
    Write-Host "      $($wrapperProc.CommandLine)" -ForegroundColor Yellow
} else {
    $stopWrapper = $true
    Write-Host "  wrapper $WrapperPid : proven (in suspect ancestry, cmd.exe, created $($wrapperProc.CreationDate))"
    Write-Host "      $($wrapperProc.CommandLine)"
}
if ($wrapperProc -and -not $stopWrapper) {
    Write-Host "  the wrapper will be LEFT RUNNING. The suspects are still stopped." -ForegroundColor Yellow
}

if ($WhatIfOnly) { Write-Host "-WhatIfOnly set; stopping nothing." -ForegroundColor Yellow; exit 0 }

# -- 5. stop child first, then parent, then the proven wrapper ---------------
$toStop = @($SuspectPids)
if ($stopWrapper) { $toStop += $WrapperPid }
foreach ($target in $toStop) {
    $p = Get-Process -Id $target -ErrorAction SilentlyContinue
    if (-not $p) { Write-Host "  $target already gone"; continue }
    try { Stop-Process -Id $target -Force -ErrorAction Stop; Write-Host "  stopped $target" }
    catch { Write-Host "  could NOT stop $target : $($_.Exception.Message)" -ForegroundColor Yellow }
}

# -- 6. the duplicate must be gone, and must stay gone -----------------------
# Watched rather than sampled once: a supervised thing that is going to come
# back comes back within seconds, and a single check taken half a second after
# the kill would report success either way.
$problems = @()
for ($tick = 0; $tick -lt 6; $tick++) {
    Start-Sleep -Seconds 2
    $problems = @()
    foreach ($target in $SuspectPids) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$target" -ErrorAction SilentlyContinue
        if ($p) { $problems += "suspect $target is still running ($($p.Name), created $($p.CreationDate))" }
    }
    if ($stopWrapper) {
        $w = Get-CimInstance Win32_Process -Filter "ProcessId=$WrapperPid" -ErrorAction SilentlyContinue
        if ($w) { $problems += "wrapper $WrapperPid is still running" }
    }
    # A replacement launched by the wrapper we validated -- either still
    # parented to it, or orphaned but still carrying it as its recorded parent.
    $spawn = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$WrapperPid" -ErrorAction SilentlyContinue |
               Where-Object { $SuspectPids -notcontains [int]$_.ProcessId })
    foreach ($sp in $spawn) {
        $problems += "a replacement appeared under wrapper ${WrapperPid}: pid $($sp.ProcessId) ($($sp.Name), created $($sp.CreationDate))"
    }
    if ($problems.Count -eq 0 -and $tick -ge 2) { break }
}
if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "DUPLICATE NOT CLEARED:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "The live backend was not touched. Re-run the identification before trying again."
    exit 3
}
Write-Host "duplicate cleared: both suspects gone, no replacement under the wrapper."

# -- 7. the live backend must have survived ----------------------------------
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
