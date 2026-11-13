@echo off
REM Windows: double-click or run from a terminal.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
echo Open http://localhost:8000
.venv\Scripts\uvicorn app.main:app --reload --port 8000
