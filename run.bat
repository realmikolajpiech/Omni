@echo off
setlocal

echo Starting Omni...

:: Check if Python is available
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python is not installed or not in PATH.
    pause
    exit /b 1
)

:: Check dependencies (Fast check)
echo Checking dependencies...
python setup.py
if %ERRORLEVEL% NEQ 0 (
    echo Error setting up dependencies!
    pause
    exit /b %ERRORLEVEL%
)

:: Kill previous instances of Omni only
:: Note: This tries to kill python processes launched with script name 'run.py' or 'listener.py'
:: Windows doesn't easily allow filtering by command line args in taskkill without complex WMI.
:: So we rely on the app's internal checks or user manually closing.
:: But to be safe for "restart", we can try to find them.
wmic process where "CommandLine like '%%run.py%%' and Name='python.exe'" call terminate >nul 2>&1
wmic process where "CommandLine like '%%listener.py%%' and Name='python.exe'" call terminate >nul 2>&1

:: Set environment variables
set TRANSFORMERS_VERBOSITY=error
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONUTF8=1

:: Run the application
python run.py

if %ERRORLEVEL% NEQ 0 (
    echo Omni crashed with error code %ERRORLEVEL%
    pause
)
