@echo off
REM ============================================================
REM AI Hub — Windows Launcher
REM
REM La terminal permanece abierta mostrando logs en tiempo real.
REM Todo el output se graba en logs\session_FECHA.log.
REM Si la GUI crashea, el traceback queda visible y guardado.
REM
REM Bootstrap:
REM   1. Verificar nvidia-smi (GPU NVIDIA requerida)
REM   2. Localizar uv  (o descargarlo via PowerShell)
REM   3. Verificar / crear venv de la UI con PySide6
REM   4. Lanzar GUI con output duplicado a terminal + log file
REM ============================================================

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
REM Quitar trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "TOOLS_DIR=%SCRIPT_DIR%\tools"
set "UI_VENV=%SCRIPT_DIR%\.ui_venv"
set "UI_SCRIPT=%SCRIPT_DIR%\gui\main.py"
set "LOGS_DIR=%SCRIPT_DIR%\..\logs"
set "UV_BIN=%TOOLS_DIR%\uv.exe"

echo.
echo ============================================================
echo              AI Hub Launcher
echo ============================================================
echo.

REM ── Step 1: GPU NVIDIA ───────────────────────────────────────
echo   [1/4] Verificando GPU NVIDIA...

where nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   ERROR: nvidia-smi no encontrado.
    echo   Se requiere GPU NVIDIA con drivers instalados.
    echo   Descargar drivers: https://www.nvidia.com/drivers
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%G in ('nvidia-smi --query-gpu=name --format=csv^,noheader^,nounits 2^>nul') do (
    set "GPU_NAME=%%G"
    goto :gpu_found
)
echo   ERROR: nvidia-smi ejecuto pero no detecto GPU.
pause
exit /b 1

:gpu_found
echo   OK  GPU: %GPU_NAME%

REM ── Step 2: uv ───────────────────────────────────────────────
echo   [2/4] Verificando uv...

if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

if not exist "%UV_BIN%" (
    echo   Descargando uv...
    powershell -NoProfile -Command ^
        "Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%TOOLS_DIR%\uv.zip'" 2>nul
    powershell -NoProfile -Command ^
        "Expand-Archive -Path '%TOOLS_DIR%\uv.zip' -DestinationPath '%TOOLS_DIR%' -Force" 2>nul
    del "%TOOLS_DIR%\uv.zip" 2>nul
)

if not exist "%UV_BIN%" (
    echo   ERROR: No se pudo descargar uv.
    echo   Verificar conexion a internet o instalar manualmente desde:
    echo   https://github.com/astral-sh/uv/releases
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('"%UV_BIN%" --version 2^>nul') do echo   OK  %%V

REM ── Step 3: UI venv + dependencias ──────────────────────────
echo   [3/4] Verificando entorno de la UI...

set "UI_PYTHON=%UI_VENV%\Scripts\python.exe"

if not exist "%UI_PYTHON%" (
    echo   Creando venv de la UI...
    "%UV_BIN%" venv --python 3.13 "%UI_VENV%" 2>nul
    if %errorlevel% neq 0 (
        "%UV_BIN%" venv "%UI_VENV%"
    )
)

"%UI_PYTHON%" -c "import PySide6, requests, numpy, safetensors" 2>nul
if %errorlevel% neq 0 (
    echo   Instalando dependencias de la UI...
    "%UV_BIN%" pip install --python "%UI_PYTHON%" ^
        PySide6 requests numpy safetensors
    if %errorlevel% neq 0 (
        echo   ERROR instalando dependencias.
        pause
        exit /b 1
    )
    echo   OK  Dependencias instaladas
)

if not exist "%UI_PYTHON%" (
    echo   ERROR: No se pudo preparar el entorno de la UI.
    pause
    exit /b 1
)

REM ── Variables de entorno ─────────────────────────────────────
set "AI_HUB_UV_PATH=%UV_BIN%"
set "AI_HUB_TOOLS_DIR=%TOOLS_DIR%"
set "UV_CACHE_DIR=%SCRIPT_DIR%\..\cache\uv"
set "UV_LINK_MODE=hardlink"
set "PYTHONUNBUFFERED=1"

REM ── Log de sesion ─────────────────────────────────────────────
if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set "DT=%%I"
set "SESSION_LOG=%LOGS_DIR%\session_%DT:~0,4%-%DT:~4,2%-%DT:~6,2%_%DT:~8,2%-%DT:~10,2%-%DT:~12,2%.log"

REM Limpiar logs viejos (mantener 10) via PowerShell
powershell -NoProfile -Command ^
    "Get-ChildItem '%LOGS_DIR%\session_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item -Force" 2>nul

REM ── Lanzar GUI ───────────────────────────────────────────────
echo   [4/4] Iniciando AI Hub...
echo.
echo   Log: %SESSION_LOG%
echo.
echo   ============================================================
echo.

REM PowerShell Tee-Object duplica output: terminal + archivo de log
powershell -NoProfile -Command ^
    "& '%UI_PYTHON%' '%UI_SCRIPT%' 2>&1 | Tee-Object -FilePath '%SESSION_LOG%'; exit $LASTEXITCODE"

set "EXIT_CODE=%errorlevel%"

echo.
echo   ============================================================
echo.

if %EXIT_CODE% neq 0 (
    echo   AI Hub termino con error ^(codigo: %EXIT_CODE%^)
    echo   Log guardado en: %SESSION_LOG%
) else (
    echo   AI Hub cerrado correctamente.
)

echo.
pause
