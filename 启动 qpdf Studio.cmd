@echo off
setlocal
set "STUDIO_DIR=%~dp0"
set "PYTHONW=%STUDIO_DIR%..\..\..\.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=%STUDIO_DIR%.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW%" (
    where pythonw.exe >nul 2>&1
    if errorlevel 1 (
        echo qpdf Studio requires Python and its dependencies.
        echo Install: python -m pip install -e "%STUDIO_DIR%"
        echo See README.md for setup.
        pause
        exit /b 1
    )
    set "PYTHONW=pythonw.exe"
)
start "" /D "%STUDIO_DIR%" "%PYTHONW%" "%STUDIO_DIR%launch_desktop.pyw"
if errorlevel 1 (
    echo Failed to start qpdf Studio.
    pause
    exit /b 1
)
exit /b 0
