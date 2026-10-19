@echo off
REM Starts the dashboard at http://localhost:8501
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup_windows.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
REM Windows has no OS-level sandbox. Local trusted mode lets YOUR OWN Python strategies run in a separate,
REM restricted process (import allowlist, no file/network APIs, timeout). Do not run code from untrusted sources.
REM Set to 0 to disable Python strategy execution on this PC (Pine Script keeps working).
if "%AMW_ALLOW_UNSANDBOXED%"=="" set AMW_ALLOW_UNSANDBOXED=1
REM Run scheduled scans/reports while the app is open (set to 0 to disable)
if "%AMW_SCHEDULER%"=="" set AMW_SCHEDULER=1
REM Restart a FYERS live feed you left running when the app opens (still needs today's FYERS login)
if "%AMW_FEED_AUTORESUME%"=="" set AMW_FEED_AUTORESUME=1
python -m streamlit run Home.py
