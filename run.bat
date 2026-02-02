@echo off
setlocal

echo Starting Omni...

:: Check dependencies using the setup script
echo Checking dependencies...
python setup.py
if %ERRORLEVEL% NEQ 0 (
    echo Error setting up dependencies!
    pause
    exit /b %ERRORLEVEL%
)

:: Kill previous instances (optional, might fail if not running, suppress output)
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Omni*" >nul 2>&1

:: Set environment variables to suppress warnings
set TRANSFORMERS_VERBOSITY=error
set HF_HUB_DISABLE_SYMLINKS_WARNING=1

:: Run the application
python run.py

pause
