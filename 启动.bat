@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title GPT Auto Register

set "APP_PORT=23457"
set "CF_PORT=8001"
set "GPT_REGISTER_PORT=%APP_PORT%"

echo.
echo ================================================
echo  GPT Auto Register - One-Click Start
echo  Auto-detect env / venv / install dependencies
echo  Main service: %APP_PORT%   CF Solver: %CF_PORT%
echo ================================================
echo.

rem ---------- 0/6 Clean leftover processes ----------
echo [0/6] Cleaning leftover processes...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":%APP_PORT% "') do (
    echo       Killing PID %%P on port %APP_PORT%
    taskkill /f /pid %%P >nul 2>nul
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr /c:":%CF_PORT% "') do (
    echo       Killing PID %%P on port %CF_PORT%
    taskkill /f /pid %%P >nul 2>nul
)
timeout /t 1 /nobreak >nul

rem ---------- 1/6 Locate Python ----------
set "PY="
python --version >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    goto :py_ok
)
py -3 --version >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
    goto :py_ok
)
for %%V in (314 313 312 311 310) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :py_ok
    )
)
if exist "C:\Python313\python.exe" (
    set "PY=C:\Python313\python.exe"
    goto :py_ok
)
if exist "C:\Python311\python.exe" (
    set "PY=C:\Python311\python.exe"
    goto :py_ok
)
echo [ERROR] Python 3.11+ not found. Please install from https://www.python.org/downloads/
goto :failed

:py_ok
"%PY%" --version
if errorlevel 1 (
    echo [ERROR] Python cannot run
    goto :failed
)
echo [1/6] Python detected OK

rem ---------- 2/6 Create virtual env ----------
if exist ".venv\Scripts\python.exe" (
    echo [2/6] Virtual env already exists
    goto :venv_ok
)
echo [2/6] Creating virtual env...
"%PY%" -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create virtual env
    goto :failed
)

:venv_ok
set "VENV_PY=.venv\Scripts\python.exe"
echo       Virtual env ready

rem ---------- 3/6 Install dependencies ----------
echo [3/6] Installing dependencies...
"%VENV_PY%" -m pip install --quiet --upgrade pip 2>nul
"%VENV_PY%" -m pip install --quiet fastapi uvicorn httpx loguru psutil 2>nul
if errorlevel 1 (
    echo [WARN] Partial dependency install failed, trying full install...
    "%VENV_PY%" -m pip install fastapi uvicorn httpx loguru psutil
    if errorlevel 1 (
        echo [ERROR] Dependency install failed
        goto :failed
    )
)

rem Install camoufox (needed by CF solver)
echo       Installing camoufox (CF solver)...
"%VENV_PY%" -m pip install --quiet "camoufox[fetch]" 2>nul
if errorlevel 1 (
    echo [WARN] camoufox install failed, CF solver may be unavailable
) else (
    rem Check camoufox data downloaded
    "%VENV_PY%" -c "import os; d1=os.path.join(os.path.expanduser('~'),'.camoufox'); d2=os.path.join(os.path.expanduser('~'),'.cache','camoufox'); exit(0 if (os.path.isdir(d1) and os.listdir(d1)) or (os.path.isdir(d2) and os.listdir(d2)) else 1)" 2>nul
    if errorlevel 1 (
        echo       Downloading camoufox browser data...
        "%VENV_PY%" -m camoufox fetch 2>nul
        if errorlevel 1 (
            echo [WARN] camoufox data download failed
        )
    )
)
echo       Dependencies installed

rem ---------- 4/6 Check frontend ----------
if exist "web_dist\index.html" (
    echo [4/6] Frontend already built, skip
    goto :backend
)
echo [4/6] Frontend not built, using built-in page...
if not exist "web_dist" mkdir web_dist

:backend
rem ---------- 5/6 Start CF Solver ----------
echo [5/6] Starting CF Solver (port %CF_PORT%)...
start /b "" "%VENV_PY%" cf_solver\boterdrop_wrapper.py >nul 2>nul
timeout /t 3 /nobreak >nul
netstat -ano | findstr LISTENING | findstr /c:":%CF_PORT% " >nul 2>nul
if errorlevel 1 (
    echo [WARN] CF Solver may not have started, CF verification will be unavailable
) else (
    echo       CF Solver started OK
)

rem ---------- 6/6 Start main service ----------
echo [6/6] Starting GPT Auto Register (port %APP_PORT%)...
echo.
echo   Web console: http://localhost:%APP_PORT%
echo   CF Solver:   http://localhost:%CF_PORT%
echo   Close this window to stop the service
echo.

set "RESTART_COUNT=0"
:service_loop
"%VENV_PY%" main.py
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
    goto :end
)
set /a RESTART_COUNT+=1
echo.
echo [RESTART] Service exited abnormally (code %EXIT_CODE%), restarting in 3s (attempt %RESTART_COUNT%)...
echo   To stop completely, just close this window
echo.
timeout /t 3 /nobreak >nul
goto :service_loop

:failed
echo.
echo Startup failed. Please check the error message above.
pause
exit /b 1

:end
echo.
echo Service stopped.
pause
exit /b 0
