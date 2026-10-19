@echo off
REM Starts the REST API at http://127.0.0.1:8000 (docs at /docs)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python api_server.py
