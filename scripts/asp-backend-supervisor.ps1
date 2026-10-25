<#
  AI Sports Predictor - local backend supervisor (127.0.0.1:8010).

  Source of truth for the process that the Windows Task Scheduler task
  "ASP Backend 8010" runs via C:\Users\james\AppData\Local\asp\start-backend.cmd.

  What it fixes relative to the previous plain .cmd loop:

    1. The interpreter is pinned to the repository virtual environment by
       ABSOLUTE path, and the working directory is set explicitly rather than
       inherited. The venv is created from Anaconda (pyvenv.cfg
       home = C:\Users\james\anaconda3), so the venv launcher legitimately
       re-executes anaconda3\python.exe as a child WITH the venv prefix.
       Invoking anaconda3\python.exe directly is what breaks: bare Anaconda has
       uvicorn but NOT pydantic_settings, so the app dies at import and a fixed
       10-second loop respawns it forever.

    2. Preflight. The environment is verified BEFORE uvicorn starts. A broken
       environment exits once, with the reason, instead of crash-looping.

    3. Bounded log. The supervisor -- not the child -- owns the log handle, so
       it can rotate mid-run. Total on-disk log is capped.

    4. Crash-burst backoff. Fast repeated exits back off exponentially and then
       give up, leaving the slower OS-level task restart policy in charge.

    5. Single owner. If port 8010 is already being served, the supervisor
       refuses to start a second backend.

  This script only starts a server. It runs no migration, no provider call, and
  no analytical work.
#>
[CmdletBinding()]
param(
  [string]$RepoRoot       = 'C:\Users\james\ai-sports-predictor',
  [int]   $Port           = 8010,
  [string]$LogPath        = 'C:\Users\james\AppData\Local\asp\backend.log',
  [int]   $MaxLogBytes    = 10485760,  # rotate the active log at 10 MB
  [int]   $KeepRotations  = 5,         # backend.log.1 .. .5  => <= ~60 MB total
  [int]   $HealthySeconds = 60,        # a run at least this long clears the crash counter
  [int]   $MaxFastCrashes = 8,         # give up after this many consecutive fast crashes
  [string]$OddsCollectionOwner = 'asp-backend-8010'  # must equal ODDS_COLLECTION_OWNER in .env
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$writer = $null

function Open-LogWriter {
  # FileShare.ReadWrite matters: the default StreamWriter share mode locks every
  # other writer out of the log, which would silently discard lines from the
  # wrapper or from a stray second supervisor -- exactly the evidence you need
  # when diagnosing a restart problem.
  $stream = New-Object System.IO.FileStream(
    $LogPath,
    [System.IO.FileMode]::Append,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::ReadWrite)
  $script:writer = New-Object System.IO.StreamWriter($stream, (New-Object System.Text.UTF8Encoding($false)))
  $script:writer.AutoFlush = $true
}

function Close-LogWriter {
  if ($null -ne $script:writer) { $script:writer.Flush(); $script:writer.Close(); $script:writer = $null }
}

function Write-Line {
  param([string]$Text)
  if ($null -eq $script:writer) { Open-LogWriter }
  $script:writer.WriteLine($Text)
}

function Write-Log {
  param([string]$Message)
  Write-Line ('[{0}] [supervisor] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Invoke-LogRotation {
  <# Rotate when the active log exceeds the cap. The oldest generation is
     dropped, bounding total log bytes at (KeepRotations + 1) * MaxLogBytes.
     Safe to call mid-run: the supervisor owns the only writer handle. #>
  try {
    if (-not (Test-Path -LiteralPath $LogPath)) { return }
    $size = if ($null -ne $script:writer) { $script:writer.BaseStream.Length }
            else { (Get-Item -LiteralPath $LogPath).Length }
    if ($size -lt $MaxLogBytes) { return }

    Close-LogWriter
    $oldest = "$LogPath.$KeepRotations"
    if (Test-Path -LiteralPath $oldest) { Remove-Item -LiteralPath $oldest -Force }
    for ($i = $KeepRotations - 1; $i -ge 1; $i--) {
      $src = "$LogPath.$i"
      if (Test-Path -LiteralPath $src) { Move-Item -LiteralPath $src -Destination "$LogPath.$($i + 1)" -Force }
    }
    Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
    Open-LogWriter
    Write-Log ('rotated log at {0} bytes (keeping {1} generations)' -f $size, $KeepRotations)
  } catch {
    try { Open-LogWriter } catch { }
    Write-Host "[supervisor] log rotation failed: $($_.Exception.Message)"
  }
}

function Test-Preflight {
  <# Verify the interpreter really is the repository venv and really can import
     what the app needs. Only then is uvicorn expected to boot. #>
  if (-not (Test-Path -LiteralPath $python)) {
    Write-Log "FATAL preflight: venv interpreter not found at $python"
    return $false
  }
  if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'server\app\main.py'))) {
    Write-Log "FATAL preflight: server\app\main.py not found under $RepoRoot"
    return $false
  }

  # Written to a file rather than passed with -c so no quoting layer can mangle it.
  $probePath = Join-Path $env:TEMP 'asp-supervisor-preflight.py'
  $probe = @'
import sys
expected = sys.argv[1].lower()
if not sys.prefix.lower().startswith(expected):
    print("PREFLIGHT_FAIL wrong interpreter prefix: %s" % sys.prefix)
    raise SystemExit(2)
import pydantic_settings, uvicorn, fastapi  # noqa: F401
print("PREFLIGHT_OK prefix=%s pydantic_settings=%s" % (sys.prefix, pydantic_settings.__version__))
'@
  Set-Content -LiteralPath $probePath -Value $probe -Encoding UTF8

  $out = & $python $probePath (Join-Path $RepoRoot '.venv') 2>&1
  $ok = ($LASTEXITCODE -eq 0) -and (@($out) -match 'PREFLIGHT_OK')
  if ($ok) {
    Write-Log ('preflight ok: {0}' -f (@($out) -match 'PREFLIGHT_OK')[0])
  } else {
    Write-Log "FATAL preflight failed for ${python}:"
    foreach ($line in @($out)) { Write-Log "  $line" }
    Write-Log '  The app requires the repository venv; bare Anaconda lacks pydantic_settings.'
  }
  return [bool]$ok
}

function Get-PortOwner {
  <# PID already listening on $Port, or $null. #>
  try {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
              Select-Object -First 1
    if ($null -eq $conn) { return $null }
    return $conn.OwningProcess
  } catch {
    if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') { return $null }
    throw "Port ownership is unverified; refusing to launch or reclaim: $($_.Exception.Message)"
  }
}

function Test-OwnDescendant {
  <# True when the process holding the port is a descendant of THIS supervisor.

     The child is launched as cmd.exe -> venv python -> uvicorn python, so the
     holder is a grandchild, not a child; the ancestry is walked rather than
     compared one level. A bounded walk, because a corrupted parent chain must
     not spin. #>
  param([int]$HolderId)
  $id = $HolderId
  for ($depth = 0; $depth -lt 8; $depth++) {
    if ($id -eq $PID) { return $true }
    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=$id") -ErrorAction SilentlyContinue
    if (-not $proc -or -not $proc.ParentProcessId -or $proc.ParentProcessId -eq 0) { return $false }
    $id = [int]$proc.ParentProcessId
  }
  return $false
}

function Test-BackendHealthy {
  <# True only if the process holding the port answers its own readiness
     endpoint. Deliberately short and deliberately fail-closed-to-unhealthy: a
     backend that cannot answer in three seconds is not serving anything, and
     the cost of being wrong is one supervised restart. #>
  param([int]$Port)
  try {
    $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/readyz" -f $Port) `
         -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
  } catch {
    return $false
  }
}

function Start-Backend {
  <# Run uvicorn to completion, streaming its merged output into the rotating
     log, and return its exit code.

     The child is launched through cmd.exe with `2>&1` so stdout and stderr
     arrive as ONE stream that can be read synchronously. Reading both streams
     of a bare process from a single thread deadlocks when either pipe fills. #>
  $inner = '"{0}" -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port {1} 2>&1' -f $python, $Port

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName               = "$env:ComSpec"
  $psi.Arguments              = '/c "' + $inner + '"'
  $psi.WorkingDirectory       = $RepoRoot
  $psi.UseShellExecute        = $false
  $psi.CreateNoWindow         = $true
  $psi.RedirectStandardOutput = $true

  # SOLE OWNER OF SPORTSBOOK CREDIT SPEND.
  # Every backend on this host reads the same .env, and this host has
  # repeatedly had three or four listening at once. The odds collector matches
  # this token against ODDS_COLLECTION_OWNER in .env and refuses to schedule
  # any credit-spending job unless they agree, so ownership follows the
  # supervised process rather than whoever happens to be running.
  $psi.EnvironmentVariables['ASP_ODDS_COLLECTION_OWNER'] = $OddsCollectionOwner

  # SOLE OWNER OF THE SCHEDULER -- and, unlike the token above, unique to this
  # launch. That one is the same string in every backend on this host, so a
  # process that outlives its supervisor keeps presenting a valid copy of it.
  # That is exactly what happened: a tree survived a previous run of this task,
  # lost port 8010, became invisible to the port guard above, and went on
  # running a second full scheduler against the same database for two days.
  #
  # The claim is written here, immediately before the launch that owns it, and
  # never on the early-exit paths above -- a supervisor that decides it has
  # nothing to do must not take ownership away from the child already serving.
  # The backend re-reads this file before every scheduled job and retires its
  # own scheduler when it no longer matches, so a superseded process stands
  # down by itself instead of waiting to be found.
  $instance = [guid]::NewGuid().ToString('N')
  $runtimeDir = Join-Path $RepoRoot '.runtime'
  if (-not (Test-Path -LiteralPath $runtimeDir)) {
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
  }
  $ownerFile = Join-Path $runtimeDir 'scheduler-owner'
  Set-Content -LiteralPath $ownerFile -Value $instance -Encoding ascii -NoNewline
  $psi.EnvironmentVariables['ASP_SCHEDULER_INSTANCE'] = $instance
  Write-Log ('scheduler instance file sha256={0} claimed in {1}' -f (Get-FileHash -LiteralPath $ownerFile -Algorithm SHA256).Hash.ToLowerInvariant(), $ownerFile)

  $proc = [System.Diagnostics.Process]::Start($psi)
  Write-Log ('uvicorn launched (cmd pid={0}) on 127.0.0.1:{1}' -f $proc.Id, $Port)

  $lines = 0
  while ($null -ne ($line = $proc.StandardOutput.ReadLine())) {
    Write-Line $line
    $lines++
    if (($lines % 200) -eq 0) { Invoke-LogRotation }
  }
  $proc.WaitForExit()
  return $proc.ExitCode
}

# ---------------------------------------------------------------- main loop --
$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
Open-LogWriter

Write-Log "supervisor starting v2 (repo=$RepoRoot port=$Port pid=$PID)"

$fastCrashes = 0
try {
  while ($true) {
    Invoke-LogRotation

    if (-not (Test-Preflight)) {
      Write-Log 'exiting; the task restart policy will retry on its own slower schedule'
      exit 1
    }

    $owner = Get-PortOwner
    if ($null -ne $owner) {
      # A held port means one of two very different things, and treating them
      # the same is what produced the long-lived orphan this guard was meant to
      # prevent: stopping the task kills the powershell and cmd wrappers but
      # leaves the elevated uvicorn holding 8010, after which every later run
      # of this supervisor politely refused to start and the stale process kept
      # serving -- and kept running its schedulers -- for days.
      #
      # So ask the holder whether it is actually serving. A healthy backend is
      # a genuine duplicate and is left alone. One that will not answer its own
      # readiness endpoint is this task's own wreckage, and this supervisor
      # runs elevated, so it is the only thing that can clear it.
      # A HEALTHY holder is still not necessarily the right one. Stopping the
      # task kills these wrappers but leaves the uvicorn grandchild running and
      # answering /readyz, so "healthy" was enough to make every later run
      # refuse -- and the machine served superseded code for days while the
      # task reported success. This port belongs to this task, so the question
      # is not whether the holder is alive but whether it is OURS.
      if ((Test-BackendHealthy -Port $Port) -and (Test-OwnDescendant -HolderId $owner)) {
        Write-Log "port $Port belongs to this supervisor's own child; exiting"
        exit 0
      }
      # Healthy OR unready foreign holders may still have active worker jobs.
      # Only an explicitly requested, actually fenced and drained owner is reclaimed.
      $requestPath = Join-Path $RepoRoot '.runtime/backend-maintenance-request.json'
      if (-not (Test-Path -LiteralPath $requestPath)) {
        Write-Log 'foreign holder has no explicit safe-maintenance request; leaving it untouched'
        exit 0
      }
      $handoff=@(& $python (Join-Path $RepoRoot 'scripts/asp-safe-handoff-probe.py') --repo $RepoRoot --request $requestPath 2>&1)
      if ($LASTEXITCODE -ne 0) {
        foreach($line in $handoff){Write-Log "handoff qualification: $line"}
        Write-Log 'handoff not qualified; refusing to kill an active prior owner'
        exit 0
      }
      $qualified=$handoff[-1]|ConvertFrom-Json
      $age=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse($qualified.observed_at_utc)).TotalSeconds
      if(-not $qualified.ready -or -not $qualified.fenced_and_drained -or $age -gt 2){throw 'Fenced/drained proof absent or expired'}
      if((Get-PortOwner) -ne $owner){throw 'Port owner changed after qualification'}
      Write-Log ("fenced owner drained; proof={0} age={1:N3}s; original supervisor reclaim pid={2}" -f $qualified.proof_sha256,$age,$owner)
      try {
        Stop-Process -Id $owner -Force -ErrorAction Stop
        Start-Sleep -Seconds 3
        Write-Log "reclaimed port $Port from stale pid $owner"
      } catch {
        Write-Log "could not reclaim port $Port from pid $owner : $($_.Exception.Message). Exiting."
        exit 1
      }
      if ($null -ne (Get-PortOwner)) {
        Write-Log "port $Port is still held after reclaim; exiting rather than starting a second backend."
        exit 1
      }
    }

    $startedAt = Get-Date
    $code = Start-Backend
    $ranSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-Log ('uvicorn exited code={0} after {1}s' -f $code, $ranSeconds)

    if ($ranSeconds -ge $HealthySeconds) {
      $fastCrashes = 0
    } else {
      $fastCrashes++
      if ($fastCrashes -ge $MaxFastCrashes) {
        Write-Log ('{0} consecutive fast crashes; exiting instead of looping. Inspect the traceback above.' -f $fastCrashes)
        exit 1
      }
    }

    $delay = [int][Math]::Min(10 * [Math]::Pow(2, [Math]::Max(0, $fastCrashes - 1)), 300)
    Write-Log ('restarting in {0}s (consecutive fast crashes: {1})' -f $delay, $fastCrashes)
    Start-Sleep -Seconds $delay
  }
} finally {
  Close-LogWriter
}
