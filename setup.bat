@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Checking for Python 3.12 ...
py -3.12 --version
if errorlevel 1 (
  echo ERROR: Python 3.12 was not found.
  echo Install Python 3.12 from https://www.python.org/downloads/ and enable "Add python.exe to PATH".
  pause
  exit /b 1
)

echo [2/4] Creating virtual environment ...
if not exist "venv\Scripts\python.exe" (
  py -3.12 -m venv venv
  if errorlevel 1 goto :error
) else (
  echo Existing virtual environment found.
)

echo [3/4] Installing libraries ...
"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/4] Initializing database and checking setup ...
"venv\Scripts\python.exe" cli.py init
if errorlevel 1 goto :error
"venv\Scripts\python.exe" cli.py doctor
if errorlevel 1 goto :error

echo.
echo Setup complete. In VS Code select: .\venv\Scripts\python.exe
exit /b 0

:error
echo.
echo ERROR: Setup failed. Read the error above, check your internet connection, and try .\setup.bat again.
pause
exit /b 1
