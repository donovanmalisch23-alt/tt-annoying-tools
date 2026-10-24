@echo off
REM ====================================================================
REM  Simple TT Media - start the bot on Windows.
REM  Uses the .venv created by setup.bat if present.
REM ====================================================================

setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo No virtual environment found. Run windows\setup.bat first.
)

if not exist "config.toml" (
    echo config.toml not found. Run windows\setup.bat to create it.
    pause
    exit /b 1
)

python -m ttmedia --config config.toml
pause
endlocal
