@echo off
REM ============================================================
REM AI Hub - Windows Launcher
REM Self-bootstrapping: finds Python, launches hub.py directly
REM No venv needed for the hub itself (zero dependencies)
REM ============================================================

setlocal enabledelayedexpansion

echo.
echo ========================================
echo            AI Hub Launcher
echo ========================================
echo.

REM Step 1: Find Python 3.8+
echo   Buscando Python...

set "PYTHON="

REM Try common Python locations
for %%P in (python3 python py) do (
    where %%P >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%V in ('%%P -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul') do (
            set "version=%%V"
        )
        for /f "tokens=*" %%M in ('%%P -c "import sys; print(sys.version_info.major)" 2^>nul') do (
            set "major=%%M"
        )
        for /f "tokens=*" %%N in ('%%P -c "import sys; print(sys.version_info.minor)" 2^>nul') do (
            set "minor=%%N"
        )

        if "!major!"=="3" if !minor! geq 8 (
            set "PYTHON=%%P"
            echo   Python encontrado: %%P ^(!version!^)
            goto :found_python
        )
    )
)

REM Also try py launcher
where py >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%V in ('py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2^>nul') do (
        set "version=%%V"
        set "PYTHON=py -3"
        echo   Python encontrado: py -3 ^(!version!^)
        goto :found_python
    )
)

echo.
echo   ERROR: Python 3.8+ no encontrado
echo.
echo   Instalar Python desde: https://www.python.org/downloads/
echo   Asegurar marcar "Add Python to PATH"
echo.
pause
exit /b 1

:found_python

REM Step 2: Launch hub.py
echo   Iniciando AI Hub...
echo.

%PYTHON% "%~dp0hub.py" %*

if %errorlevel% neq 0 (
    echo.
    echo   Error ejecutando AI Hub ^(codigo: %errorlevel%^)
    pause
)
