@echo off
rem Use Windows native commands; resolve main port from config.json (fallback 23457)
%SystemRoot%\System32\chcp.com 65001 >nul
cd /d "%~dp0"
echo Stopping GPT Auto Register service...

rem Resolve main port from config.json (v3.1: matches start.bat, supports custom port)
rem NOTE: python writes port to a temp file, "set /p" reads it (for/f breaks on python parens in cmd).
set "APP_PORT=23457"
if exist "config.json" if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import json;open('_port_tmp.txt','w').write(str(json.load(open('config.json',encoding='utf-8')).get('port',23457)))" 2>nul
    if exist "_port_tmp.txt" (
        set /p APP_PORT=<_port_tmp.txt
        del "_port_tmp.txt"
    )
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":%APP_PORT% "') do (
    echo   Killing main service PID %%P on port %APP_PORT%
    taskkill /f /pid %%P >nul 2>nul
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":8001 "') do (
    echo   Killing CF Solver PID %%P
    taskkill /f /pid %%P >nul 2>nul
)
echo Service stopped.
pause
