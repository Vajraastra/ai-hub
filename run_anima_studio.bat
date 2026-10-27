@echo off
setlocal
cd /d "%~dp0"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_PYTHON_INSTALL_DIR=%CD%\hub\tools\python"
set "UV_CACHE_DIR=%CD%\.cache\uv"
set "PYTHONUTF8=1"
echo [1/5] Buscando uv...
set "STUDIO_UV="
if exist "hub\tools\win\uv.exe" set "STUDIO_UV=%CD%\hub\tools\win\uv.exe"
if not defined STUDIO_UV for /f "delims=" %%U in ('where uv 2^>nul') do if not defined STUDIO_UV set "STUDIO_UV=%%U"
if defined STUDIO_UV goto uv_ready
echo [1/5] Instalando uv para el usuario...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto error
if exist "%USERPROFILE%\.local\bin\uv.exe" set "STUDIO_UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined STUDIO_UV goto error
:uv_ready
echo [2/5] Preparando Python administrado...
if exist "hub-webui\.venv\Scripts\python.exe" goto env_ready
"%STUDIO_UV%" venv --python 3.13 "hub-webui\.venv"
if errorlevel 1 goto error
:env_ready
echo [3/5] Verificando dependencias del Hub...
"%STUDIO_UV%" pip install --python "hub-webui\.venv\Scripts\python.exe" -r "hub-webui\requirements.txt"
if errorlevel 1 goto error
echo [4/5] Comprobando configuracion...
if defined AI_HUB_CONFIG goto config_ready
if exist "hub\hub_config.json" goto config_ready
echo Configura la carpeta de modelos desde el Hub o define AI_HUB_CONFIG con su archivo de configuracion.
goto error
:config_ready
echo [5/5] Taller Anima: http://127.0.0.1:9754
echo Inicia ComfyUI desde el Hub para generar. Cierra esta ventana con Ctrl+C al terminar.
set "STUDIO_BROWSER=--open-browser"
if /i "%~1"=="--no-browser" set "STUDIO_BROWSER="
"hub-webui\.venv\Scripts\python.exe" "hub-webui\anima_studio_poc.py" --port 9754 %STUDIO_BROWSER%
if errorlevel 1 goto error
exit /b 0
:error
echo No se pudo iniciar Taller Anima. Revisa el paso anterior.
pause
exit /b 1
