@echo off
REM ====================================================================
REM  Simple TT Media - start the bot. Double-click to run.
REM ====================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [!!] No environment found. Run Setup.bat first.
    pause & exit /b 1
)
if not exist "config.toml" (
    echo [!!] config.toml not found. Run Setup.bat first.
    pause & exit /b 1
)

ttmedia -c config.toml
echo.
echo The bot has stopped.
pause
endlocal
