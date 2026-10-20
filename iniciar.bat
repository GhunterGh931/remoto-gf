@echo off
chcp 65001 >nul
title Remoto GF
cd /d "%~dp0"
where python >nul 2>nul || (echo Python nao encontrado. Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH". & pause & exit /b)
if not exist ".venv\pronto.txt" (
  echo Preparando na primeira vez, leva uns minutos...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Falhou a instalacao. & pause & exit /b)
  echo ok> ".venv\pronto.txt"
)
start "" ".venv\Scripts\pythonw.exe" remoto.py
