@echo off
cd /d "%~dp0"

if not exist ".env" (
    copy .env.example .env
    echo Created .env — please add your ANTHROPIC_API_KEY then run this script again.
    pause
    exit /b
)

if not exist "venv" (
    echo Setting up virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -r requirements.txt -q

echo.
echo  App running at http://localhost:5000
echo  Press Ctrl+C to stop
echo.
python app.py
