@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Проверка Python 3.12...
py -3.12 --version
if errorlevel 1 goto python_missing

echo Создание окружения...
py -3.12 -m venv .venv
if errorlevel 1 goto failed

echo Установка компонентов приложения...
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements.windows.lock
if errorlevel 1 goto failed

echo.
echo Установка завершена. Теперь откройте start-windows.bat.
pause
exit /b 0

:python_missing
echo.
echo Сначала установите Python 3.12, например командой: py install 3.12
pause
exit /b 1

:failed
echo.
echo Установка остановлена из-за ошибки выше. Скопируйте её текст.
echo Запускать приложение пока не нужно.
pause
exit /b 1
