@echo off
setlocal EnableExtensions
set "STUDIO_DIR=%~dp0"
cd /d "%STUDIO_DIR%"

if exist "%STUDIO_DIR%..\..\..\.venv\Scripts\pythonw.exe" (
    set "PYW=%STUDIO_DIR%..\..\..\.venv\Scripts\pythonw.exe"
    goto launch
)
if exist "%STUDIO_DIR%.venv\Scripts\pythonw.exe" (
    set "PYW=%STUDIO_DIR%.venv\Scripts\pythonw.exe"
    goto launch
)

set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)"') do set "PYEXE=%%I"
)
if not defined PYEXE (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)"') do set "PYEXE=%%I"
    )
)
if not defined PYEXE (
    echo 未找到 Python 3.11 或更新版本。
    echo 请先安装 Python，并勾选 Add python.exe to PATH。
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

"%PYEXE%" -c "import PySide6, pikepdf, pyhanko, pypdfium2, PIL" >nul 2>&1
if errorlevel 1 (
    echo 第一次运行，正在安装公开依赖。完成后会打开 qpdf Studio。
    "%PYEXE%" -m pip install -e "%STUDIO_DIR%"
    if errorlevel 1 (
        echo 依赖安装失败。请检查网络后重新双击本文件。
        pause
        exit /b 1
    )
)

set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"

:launch
start "" /D "%STUDIO_DIR%" "%PYW%" "%STUDIO_DIR%launch_desktop.pyw"
exit /b 0
