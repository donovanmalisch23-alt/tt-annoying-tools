@echo off
REM ====================================================================
REM  Simple TT Media - Windows setup
REM  Creates a local virtual environment, installs dependencies, and
REM  runs the interactive setup wizard to create config.toml.
REM  Double-click this file, or run it from a command prompt.
REM ====================================================================

setlocal
cd /d "%~dp0.."

REM Prefer the Python launcher (py), fall back to python on PATH.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY=python"
    ) else (
        echo Python 3.11+ was not found. Install it from https://python.org
        echo and tick "Add Python to PATH" during setup.
        pause
        exit /b 1
    )
)

echo Creating virtual environment (.venv)...
%PY% -m venv .venv
if errorlevel 1 ( echo Failed to create venv. & pause & exit /b 1 )

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )

echo.
echo Checking for ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [!!] ffmpeg not found on PATH. The TeamTalk SDK needs it to decode media.
    echo      Install it from https://ffmpeg.org/download.html and add it to PATH,
    echo      or run:  winget install Gyan.FFmpeg
) else (
    echo [ok] ffmpeg found.
)

echo.
echo Launching the setup wizard...
python setup_bot.py

echo.
echo Done. Start the bot later by double-clicking windows\run.bat
pause
endlocal
