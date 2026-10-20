@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python -m venv .venv
) else (
  py -3.12 -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
call rebuild_frontend.cmd
if errorlevel 1 goto failed
echo Ready. Double-click start_yuki.cmd.
pause
exit /b 0
:failed
echo Setup did not finish. Check Python 3.12, Node.js and network access, then retry.
pause
exit /b 1
