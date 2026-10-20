@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
if defined YUKI_PYTHON (
  start "" "%YUKI_PYTHON%" "%~dp0start_yuki.py" %*
  exit /b
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0start_yuki.py" %*
  exit /b
)
where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3 "%~dp0start_yuki.py" %*
  exit /b
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0start_yuki.py" %*
  exit /b
)
echo Python was not found. Run setup_yuki.cmd or use the standalone Alpha package.
pause
