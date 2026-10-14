@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
start "" pythonw main.py
