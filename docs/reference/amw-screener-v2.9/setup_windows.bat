@echo off
REM One-time setup for Windows 10/11. Requires Python 3.11 (python.org installer, "Add python.exe to PATH" ticked).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3.11 -m venv .venv) else (python -m venv .venv)
if errorlevel 1 (echo Could not create the virtual environment. Is Python 3.11 installed? & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if errorlevel 1 (echo Dependency installation failed. & pause & exit /b 1)
echo.
echo Strategy Lab runtimes detected on this PC:
set AMW_ALLOW_UNSANDBOXED=1
python scripts\check_runtimes.py
echo.
echo Optional: JavaScript/TypeScript need Node.js 22 (nodejs.org) + "npm install -g typescript@6.0.2"; R needs R 4.3 (cran.r-project.org).
echo Python, Pine Script, Visual builder, SQL and Lua work without extra installs.
echo Setup complete. Start the app with start_screener.bat
pause
