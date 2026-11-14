@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
if not exist "frontend\dist\index.html" goto missing
echo AI Sales Copilot: http://127.0.0.1:8765
echo Оставьте это окно открытым. Для остановки нажмите Ctrl+C.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
pause
exit /b
:missing
echo Сначала выполните установку по README.md.
pause
