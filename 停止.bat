@echo off
%SystemRoot%\System32\chcp.com 65001 >nul
echo Stopping GPT Auto Register service...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":23457 "') do (
    echo   Killing main service PID %%P
    taskkill /f /pid %%P >nul 2>nul
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":8001 "') do (
    echo   Killing CF Solver PID %%P
    taskkill /f /pid %%P >nul 2>nul
)
echo Service stopped.
pause
