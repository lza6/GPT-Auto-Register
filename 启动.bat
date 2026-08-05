@echo off
rem Use Windows native commands to avoid PATH hijack from Git for Windows GNU tools
rem (GNU timeout.exe shadows System32\timeout.exe; /t arg would be eaten -> crash)
%SystemRoot%\System32\chcp.com 65001 >nul
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
rem ping instead of timeout: immune to GNU coreutils hijack
ping -n 2 127.0.0.1 >nul

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
if exist requirements.txt (
    echo       Installing from requirements.txt...
    "%VENV_PY%" -m pip install --quiet -r requirements.txt 2>nul
    if errorlevel 1 (
        echo [WARN] requirements install failed, retrying verbose...
        "%VENV_PY%" -m pip install -r requirements.txt
        if errorlevel 1 (
            echo [ERROR] Dependency install failed
            goto :failed
        )
    )
) else (
    echo [WARN] requirements.txt not found, installing core deps manually...
    "%VENV_PY%" -m pip install fastapi uvicorn httpx loguru psutil requests curl_cffi imapclient
    if errorlevel 1 (
        echo [ERROR] Dependency install failed
        goto :failed
    )
)

rem Check camoufox browser data downloaded (needed by CF solver / browser fallback)
rem NOTE: real install dir on Windows is %LOCALAPPDATA%\camoufox\camoufox\Cache (camoufox.pkgman.INSTALL_DIR), NOT ~/.camoufox
"%VENV_PY%" -c "from camoufox.pkgman import INSTALL_DIR; b=INSTALL_DIR/'browsers'; exit(0 if (b.is_dir() and list(b.iterdir())) else 1)" 2>nul
if errorlevel 1 (
    echo       Camoufox browser data missing, installing...
    rem Prefer offline zip (tools\camoufox\*.zip); else online download; failure prints manual guide
    powershell -NoProfile -ExecutionPolicy Bypass -File install_camoufox.ps1
    if errorlevel 1 (
        echo [WARN] camoufox data install failed, CF verification may be unavailable
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
if not exist "logs" mkdir logs
for /f "delims=" %%T in ('gh auth token 2^>nul') do set "GITHUB_TOKEN=%%T"
start /b "" "%VENV_PY%" cf_solver\boterdrop_wrapper.py >>logs\cf_solver.log 2>&1
rem Poll up to 40s instead of fixed 3s (browser engine init can take a while)
set "CF_WAIT=0"
:cf_wait
ping -n 3 127.0.0.1 >nul
set /a CF_WAIT+=2
netstat -ano | findstr LISTENING | findstr /c:":%CF_PORT% " >nul 2>nul
if not errorlevel 1 goto :cf_ok
if !CF_WAIT! LSS 40 goto :cf_wait
echo [WARN] CF Solver did not start within %CF_WAIT% s, CF verification may be unavailable
echo       Check logs\cf_solver.log for details
if exist "logs\cf_solver.log" powershell -NoProfile -Command "Get-Content 'logs\cf_solver.log' -Tail 20"
goto :cf_done
:cf_ok
echo       CF Solver started OK
:cf_done

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
ping -n 4 127.0.0.1 >nul
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
