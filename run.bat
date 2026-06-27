@echo off
REM ============================================================
REM AI Hub - Windows Launcher (fuente unica de verdad)
REM
REM Arranca la WebUI (FastAPI) del hub. La terminal queda abierta
REM mostrando logs en vivo; todo se graba en logs\session_*.log.
REM
REM Bootstrap:
REM   [1/5] Verificar GPU NVIDIA (nvidia-smi)
REM   [2/5] Asegurar uv (PATH o descarga portable a hub\tools\win)
REM   [3/5] Asegurar Node.js portable (hub\tools\win\node)
REM   [4/5] Crear/auto-reparar venv de la WebUI + dependencias
REM   [5/5] Lanzar la WebUI con output duplicado a terminal + log
REM
REM Windows-first. No depende de Git Bash. Los binarios Linux de
REM hub\tools (uv/node/python ELF) NO se usan ni se tocan aqui.
REM ============================================================

setlocal enabledelayedexpansion

REM -- Resolver directorio propio y cd ahi ----------------------
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

set "WEBUI_DIR=%SCRIPT_DIR%\hub-webui"
set "TOOLS_DIR=%SCRIPT_DIR%\hub\tools\win"
set "LOGS_DIR=%SCRIPT_DIR%\logs"
set "VENV_DIR=%WEBUI_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "WEBUI_APP=%WEBUI_DIR%\app.py"
set "REQ_FILE=%WEBUI_DIR%\requirements.txt"

set "HUB_PY_VERSION=3.13"
set "NODE_VERSION=22.17.0"
set "UV_BIN=%TOOLS_DIR%\uv.exe"

REM -- Entorno uv: NO latchear a Pythons PEP 514 de otros proyectos
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_CACHE_DIR=%SCRIPT_DIR%\.cache\uv"
set "PYTHONUNBUFFERED=1"

if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

echo.
echo ============================================================
echo                    AI Hub Launcher
echo ============================================================
echo.

REM ============================================================
REM [1/5] GPU NVIDIA
REM ============================================================
echo   [1/5] Verificando GPU NVIDIA...
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo   ERROR: nvidia-smi no encontrado. Se requiere GPU NVIDIA con drivers.
    echo   Drivers: https://www.nvidia.com/drivers
    goto :fail
)
set "GPU_NAME="
for /f "tokens=*" %%G in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader^,nounits 2^>nul') do (
    if not defined GPU_NAME set "GPU_NAME=%%G"
)
if not defined GPU_NAME (
    echo   ERROR: nvidia-smi ejecuto pero no detecto GPU.
    goto :fail
)
echo   OK  GPU: %GPU_NAME%

REM ============================================================
REM [2/5] uv
REM ============================================================
echo   [2/5] Verificando uv...

REM Preferir uv del sistema si esta en PATH
where uv >nul 2>&1
if not errorlevel 1 set "UV_BIN=uv"

if not exist "%TOOLS_DIR%\uv.exe" if /i "%UV_BIN%"=="%TOOLS_DIR%\uv.exe" (
    echo   Descargando uv portable...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '%TOOLS_DIR%\uv.zip'; Expand-Archive -Path '%TOOLS_DIR%\uv.zip' -DestinationPath '%TOOLS_DIR%' -Force; Remove-Item '%TOOLS_DIR%\uv.zip' -Force } catch { exit 1 }"
    if errorlevel 1 (
        echo   ERROR: no se pudo descargar uv. Ver https://github.com/astral-sh/uv/releases
        goto :fail
    )
)

"%UV_BIN%" --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: uv no funcional.
    goto :fail
)
for /f "tokens=*" %%V in ('"%UV_BIN%" --version 2^>nul') do echo   OK  %%V

REM ============================================================
REM [3/5] Node.js portable
REM ============================================================
echo   [3/5] Verificando Node.js...
REM Path fijo: el portable siempre queda en %TOOLS_DIR%\node\node.exe
set "NODE_DIR=%TOOLS_DIR%\node"
set "NODE_BIN="
if exist "%NODE_DIR%\node.exe" set "NODE_BIN=%NODE_DIR%"

if not defined NODE_BIN (
    echo   Descargando Node.js v%NODE_VERSION% portable...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $z='%TOOLS_DIR%\node.zip'; $tmp='%TOOLS_DIR%\node_tmp'; Invoke-WebRequest -Uri 'https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip' -OutFile $z; if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }; Expand-Archive -Path $z -DestinationPath $tmp -Force; $sub = Get-ChildItem $tmp -Directory | Select-Object -First 1; if (Test-Path '%NODE_DIR%') { Remove-Item '%NODE_DIR%' -Recurse -Force }; Move-Item $sub.FullName '%NODE_DIR%'; Remove-Item $tmp -Recurse -Force; Remove-Item $z -Force } catch { exit 1 }"
    if exist "%NODE_DIR%\node.exe" set "NODE_BIN=%NODE_DIR%"
)

if defined NODE_BIN (
    set "PATH=%NODE_BIN%;%PATH%"
    set "AI_HUB_NODE_DIR=%NODE_BIN%"
    for /f "tokens=*" %%V in ('"%NODE_BIN%\node.exe" --version 2^>nul') do echo   OK  Node.js %%V
) else (
    echo   WARN  Node.js no disponible ^(continuo sin el^).
)

REM ============================================================
REM [4/5] venv de la WebUI + dependencias
REM ============================================================
echo   [4/5] Verificando entorno de la WebUI...

if not exist "%VENV_PY%" (
    echo   Creando venv [descarga Python %HUB_PY_VERSION% managed si hace falta]...
    REM --clear limpia cualquier venv residual de Linux arrastrado del disco externo
    "%UV_BIN%" venv --clear --python %HUB_PY_VERSION% "%VENV_DIR%"
    if errorlevel 1 (
        echo   ERROR: no se pudo crear el venv.
        goto :fail
    )
)

"%VENV_PY%" -c "import fastapi, uvicorn, requests, numpy, aiohttp, PIL, websockets" >nul 2>&1
if errorlevel 1 (
    echo   Instalando dependencias de la WebUI...
    "%UV_BIN%" pip install --python "%VENV_PY%" -r "%REQ_FILE%"
    if errorlevel 1 (
        echo   ERROR: fallo la instalacion de dependencias.
        goto :fail
    )
    echo   OK  Dependencias instaladas
) else (
    echo   OK  WebUI lista
)

REM -- Variables que consume el hub para lanzar apps ------------
set "AI_HUB_UV_PATH=%UV_BIN%"
set "AI_HUB_TOOLS_DIR=%TOOLS_DIR%"

REM ============================================================
REM [5/5] Lanzar WebUI
REM ============================================================
if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"

REM wmic fue removido en Windows 11 24H2+: usar PowerShell para el timestamp
set "DT="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'"`) do set "DT=%%I"
if not defined DT set "DT=session"
set "SESSION_LOG=%LOGS_DIR%\session_%DT%.log"

REM Conservar solo los ultimos 10 logs
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Get-ChildItem '%LOGS_DIR%\session_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item -Force -ErrorAction SilentlyContinue" 2>nul

echo   [5/5] Iniciando AI Hub...
echo.
echo   Log: %SESSION_LOG%
echo.
echo   ============================================================
echo.

REM Tee-Object duplica el output: terminal + archivo de log
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& '%VENV_PY%' '%WEBUI_APP%' 2>&1 | Tee-Object -FilePath '%SESSION_LOG%'; exit $LASTEXITCODE"
set "EXIT_CODE=%errorlevel%"

echo.
echo   ============================================================
echo.
if not "%EXIT_CODE%"=="0" (
    echo   AI Hub termino con error ^(codigo: %EXIT_CODE%^)
    echo   Log: %SESSION_LOG%
) else (
    echo   AI Hub cerrado correctamente.
)
echo.
pause
exit /b %EXIT_CODE%

:fail
echo.
pause
exit /b 1
