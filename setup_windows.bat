@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ==================================================
echo   NRP CORPORATE OSINT V4 - INSTALACION WINDOWS
echo ==================================================
where py >nul 2>nul
if not errorlevel 1 goto try_py
where python >nul 2>nul
if not errorlevel 1 goto try_python
echo No se encontro Python 3.11, 3.12 o 3.13. Activa "Add Python to PATH" durante la instalacion.
goto failed

:try_py
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto use_py312
py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 goto use_py313
py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 goto use_py311
echo El lanzador de Python no tiene 3.11, 3.12 o 3.13 registrado.
echo Intentando con python del PATH...
where python >nul 2>nul
if not errorlevel 1 goto try_python
echo No se encontro Python 3.11, 3.12 o 3.13.
goto failed

:use_py312
py -3.12 "scripts\setup_env.py"
goto check_result

:use_py313
py -3.13 "scripts\setup_env.py"
goto check_result

:use_py311
py -3.11 "scripts\setup_env.py"
goto check_result

:try_python
python -c "import sys; raise SystemExit(sys.version_info[:2] not in ((3,11),(3,12),(3,13)))" >nul 2>nul
if errorlevel 1 (
 echo La version de Python disponible no es compatible.
 goto failed
)
python "scripts\setup_env.py"

:check_result
if errorlevel 1 goto failed
echo.
echo Instalacion terminada.
echo Ejecuta run_dashboard_windows.bat
pause
exit /b 0

:failed
echo.
echo La instalacion no pudo completarse.
pause
exit /b 1
