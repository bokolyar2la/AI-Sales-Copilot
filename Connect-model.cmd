@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Сначала запустите Install.cmd.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" -m backend.configure_llm
pause
