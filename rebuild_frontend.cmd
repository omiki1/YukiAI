@echo off
cd /d "%~dp0app"
call npm.cmd ci --ignore-scripts --cache "%~dp0.cache\npm" --no-audit --no-fund
if errorlevel 1 exit /b 1
call npm.cmd run build
if errorlevel 1 exit /b 1
