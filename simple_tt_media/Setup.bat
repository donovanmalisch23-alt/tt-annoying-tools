@echo off
REM ====================================================================
REM  Simple TT Media - one-click setup for Windows.
REM  Creates a local Python environment, installs everything (including
REM  ffmpeg and the Deno runtime), and runs the configuration wizard.
REM  Just double-click this file.
REM ====================================================================
setlocal
cd /d "%~dp0"

REM --- find 64-bit Python ------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo [!!] Python 3.11+ was not found.
    echo     Install the 64-bit build from https://www.python.org/downloads/
    echo     and tick "Add Python to PATH" during installation.
    pause & exit /b 1
)

for /f %%A in ('%PY% -c "import struct;print(struct.calcsize('P')*8)"') do set BITS=%%A
if not "%BITS%"=="64" (
    echo [!!] Your Python is %BITS%-bit, but the bundled TeamTalk5.dll is 64-bit.
    echo     Please install 64-bit Python 3.11+ and run Setup again.
    pause & exit /b 1
)

echo Creating the local environment (.venv) ...
%PY% -m venv .venv || ( echo [!!] Could not create venv. & pause & exit /b 1 )
call ".venv\Scripts\activate.bat"

echo Installing Simple TT Media and its dependencies ...
python -m pip install --upgrade pip >nul
pip install -e . || ( echo [!!] Dependency install failed. & pause & exit /b 1 )

echo.
echo Downloading helper tools (ffmpeg, deno) ...
python -m ttmedia.bootstrap

echo.
echo Starting the configuration wizard ...
ttmedia-setup
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [!!] Setup did not finish (exit code %RC%). Scroll up for details.
    pause & exit /b %RC%
)
if not exist "config.toml" (
    echo [!!] Setup finished but config.toml was not created. Please run Setup again.
    pause & exit /b 1
)
echo [ok] All set. Double-click Start.bat to run the bot.
pause
endlocal
