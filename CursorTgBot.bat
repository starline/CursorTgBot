@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  where py >nul 2>&1 && py -3 -m venv .venv
  if not exist ".venv\Scripts\python.exe" python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3.11+ was not found. Install it from https://www.python.org/downloads/
    echo Or build the app on Windows: packaging\windows\build.ps1
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-desktop.txt"
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0launch_desktop.py" %*
  exit /b 0
)

start "" ".venv\Scripts\python.exe" "%~dp0launch_desktop.py" %*
