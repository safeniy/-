@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto install_first
".venv\Scripts\python.exe" -c "import uvicorn" >nul 2>&1
if errorlevel 1 goto install_first
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 (
  echo.
  echo Приложение завершилось с ошибкой. Скопируйте сообщение выше.
  pause
  exit /b 1
)
exit /b 0

:install_first
echo Сначала запустите install-windows.bat и дождитесь успешной установки.
pause
exit /b 1
