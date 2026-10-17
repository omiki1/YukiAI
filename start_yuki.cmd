@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
start "" "C:\Users\freeing1\anaconda3\envs\myenv\pythonw.exe" "%~dp0start_yuki.py" %*
