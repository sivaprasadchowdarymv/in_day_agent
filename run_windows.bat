@echo off
REM BharatTrade AI - one-click start on Windows (creates a virtual environment on first run)
cd /d %~dp0
if not exist .venv (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
if not exist .env copy .env.example .env
streamlit run app.py
