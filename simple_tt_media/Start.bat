@echo off
REM ====================================================================
REM  Simple TT Media - start the bot. Double-click to run.
REM  It reconnects on its own; close this window (or send the admin
REM  'quit' command) to stop it.
REM ====================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [X] No environment found. Run Setup.bat first.
    pause & exit /b 1
)
if not exist "config.toml" (
    echo [X] config.toml not found. Run Setup.bat first.
    pause & exit /b 1
)

".venv\Scripts\python.exe" -m ttmedia -c config.toml
echo.
echo The bot has stopped.
pause
endlocal
