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
echo The TeamTalk SDK (including media decoding) is bundled in sdk\ -
echo no separate SDK or ffmpeg install is needed on Windows.

echo.
echo Launching the setup wizard...
python setup_bot.py
set "WIZARD_RC=%ERRORLEVEL%"

echo.
if not "%WIZARD_RC%"=="0" (
    echo [!!] Setup did not complete successfully ^(exit code %WIZARD_RC%^).
    echo      Scroll up to see what went wrong; config.toml may not have been saved.
    pause
    exit /b %WIZARD_RC%
)
if not exist "config.toml" (
    echo [!!] Setup finished but config.toml was not created. Please re-run setup.
    pause
    exit /b 1
)

echo [ok] Setup complete. config.toml was created.
echo Start the bot by double-clicking windows\run.bat
pause
endlocal
