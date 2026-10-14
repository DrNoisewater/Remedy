@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
echo [1/3] Downloading build tools (first run can take a few minutes)...
python -m pip install -r requirements.txt pyinstaller --disable-pip-version-check --progress-bar on
if errorlevel 1 (echo. & echo Download failed. Check your internet connection and try again. & pause & exit /b 1)
echo.
echo [2/3] Preparing version details...
python make_version_info.py
echo.
echo [3/3] Building Remedy.exe ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name Remedy --icon remedy.ico --version-file version_info.txt --collect-submodules remedy main.py
if errorlevel 1 (echo. & echo Build failed. Scroll up for the error. & pause & exit /b 1)
echo.
echo Done: dist\Remedy.exe
pause
