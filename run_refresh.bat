@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\refresh_all.py > refresh_batch.log 2>&1
