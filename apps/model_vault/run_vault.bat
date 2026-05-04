@echo off
set SCRIPT_DIR=%~dp0
set HUB_DIR=%SCRIPT_DIR%..\..\hub
set TOOLS_DIR=%HUB_DIR%\tools
set UV_BIN=%TOOLS_DIR%\uv.exe
set UI_VENV=%HUB_DIR%\.ui_venv
set UI_PYTHON=%UI_VENV%\Scripts\python.exe

if not exist "%UI_PYTHON%" (
    echo Error: Hub GUI environment not found. Please run hub\run.sh first.
    pause
    exit /b 1
)

echo --- Starting Model Vault (Vault) ---
set PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%
"%UI_PYTHON%" "%SCRIPT_DIR%\main.py" %*
pause
