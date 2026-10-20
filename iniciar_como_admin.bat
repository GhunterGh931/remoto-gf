@echo off
cd /d "%~dp0"
if not exist ".venv\pronto.txt" (echo Rode o iniciar.bat primeiro. & pause & exit /b)
".venv\Scripts\python.exe" remoto.py --admin
