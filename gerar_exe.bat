@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\pronto.txt" (echo Rode o iniciar.bat primeiro. & pause & exit /b)
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\pyinstaller.exe" --noconfirm --onefile --windowed --name RemotoGF --icon icon.ico --add-data "web;web" --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import pystray._win32 --collect-submodules comtypes remoto.py
echo.
echo Pronto: dist\RemotoGF.exe
pause
