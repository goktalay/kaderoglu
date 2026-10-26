@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if not exist config.json .venv\Scripts\python.exe setup_user.py
.venv\Scripts\python.exe run.py
