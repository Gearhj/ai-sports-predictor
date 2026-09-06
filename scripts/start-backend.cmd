@echo off
REM AI Sports Predictor - launcher for the Windows Task Scheduler task
REM "ASP Backend 8010" (127.0.0.1:8010).
REM
REM This file is deliberately thin. All supervision behaviour - venv preflight,
REM bounded log rotation, crash-burst backoff, single-owner port check - lives in
REM the tracked PowerShell supervisor so it is reviewable and version controlled.
REM
REM Install a copy of this file at:
REM   C:\Users\james\AppData\Local\asp\start-backend.cmd
setlocal

set "ASP_ROOT=C:\Users\james\ai-sports-predictor"
set "ASP_SUPERVISOR=%ASP_ROOT%\scripts\asp-backend-supervisor.ps1"
set "ASP_LOG=C:\Users\james\AppData\Local\asp\backend.log"

REM Bootstrap log for this wrapper and for PowerShell's own startup output.
REM It must NOT be the main log: cmd opens a `>>` target with a deny-write share
REM mode, which would prevent the supervisor from opening backend.log itself.
set "ASP_BOOTLOG=C:\Users\james\AppData\Local\asp\supervisor-boot.log"

echo [%DATE% %TIME%] [wrapper] launching supervisor >> "%ASP_BOOTLOG%"

cd /d "%ASP_ROOT%"
if errorlevel 1 (
  echo [%DATE% %TIME%] [wrapper] FATAL cannot enter %ASP_ROOT% >> "%ASP_BOOTLOG%"
  exit /b 1
)

if not exist "%ASP_SUPERVISOR%" (
  echo [%DATE% %TIME%] [wrapper] FATAL supervisor not found: %ASP_SUPERVISOR% >> "%ASP_BOOTLOG%"
  exit /b 1
)

if not exist "%ASP_ROOT%\.venv\Scripts\python.exe" (
  echo [%DATE% %TIME%] [wrapper] FATAL venv interpreter missing >> "%ASP_BOOTLOG%"
  exit /b 1
)

REM Capture PowerShell's own stdout/stderr. Without this, a failure to even start
REM PowerShell (bad path, blocked policy, syntax error) would be invisible.
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ASP_SUPERVISOR%" -RepoRoot "%ASP_ROOT%" -Port 8010 -LogPath "%ASP_LOG%" >> "%ASP_BOOTLOG%" 2>&1
exit /b %ERRORLEVEL%
