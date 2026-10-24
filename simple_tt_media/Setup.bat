@echo off
REM ====================================================================
REM  Simple TT Media - one-click setup for Windows.
REM  Builds an isolated Python environment, installs the bot plus ffmpeg
REM  and the Deno runtime, and runs the configuration wizard.
REM  Just double-click this file.
REM ====================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================
echo   Simple TT Media - Setup
echo ============================================
echo.

REM --- locate 64-bit Python ---------------------------------------------
set "PY="
where py  >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo [X] Python 3.11+ was not found.
    echo     Install the 64-bit build from https://www.python.org/downloads/
    echo     and tick "Add Python to PATH" during installation, then re-run Setup.
    pause & exit /b 1
)

for /f %%A in ('%PY% -c "import struct;print(struct.calcsize('P')*8)"') do set "BITS=%%A"
if not "%BITS%"=="64" (
    echo [X] Your Python is %BITS%-bit, but the bundled TeamTalk5.dll is 64-bit.
    echo     Please install 64-bit Python 3.11+ and run Setup again.
    pause & exit /b 1
)

REM --- virtual environment ----------------------------------------------
echo [1/4] Creating the local environment (.venv) ...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || ( echo [X] Could not create the environment. & pause & exit /b 1 )
)
set "VPY=.venv\Scripts\python.exe"

REM --- dependencies (timeouts + retries so pip never hangs) -------------
echo [2/4] Installing the bot and its dependencies ...
"%VPY%" -m pip install --upgrade --timeout 60 --retries 3 pip >nul 2>&1
"%VPY%" -m pip install --timeout 60 --retries 3 -e . || (
    echo [X] Installing dependencies failed. Check your internet connection and re-run Setup.
    pause & exit /b 1
)

REM --- helper tools (ffmpeg, deno) --------------------------------------
echo [3/4] Downloading helper tools (ffmpeg, deno) ...
"%VPY%" -m ttmedia.bootstrap

REM --- configuration wizard ---------------------------------------------
echo [4/4] Configuration
"%VPY%" -m ttmedia.setup_wizard
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [X] Setup did not finish ^(exit code %RC%^). Scroll up to see what went wrong.
    pause & exit /b %RC%
)
if not exist "config.toml" (
    echo [X] Setup finished but config.toml was not created. Please run Setup again.
    pause & exit /b 1
)
echo [OK] All set. Double-click Start.bat to run the bot.
pause
endlocal
