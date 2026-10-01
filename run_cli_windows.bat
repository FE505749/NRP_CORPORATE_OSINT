@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_BIN=.venv-windows\Scripts\python.exe"
if not exist "%PYTHON_BIN%" set "PYTHON_BIN=.venv\Scripts\python.exe"
if not exist "%PYTHON_BIN%" (
 echo Primero ejecuta setup_windows.bat
 pause
 exit /b 1
)
"%PYTHON_BIN%" "cli.py" %*
