@echo off
REM Celery Beat - agenda check_scheduled_posts_task a cada 1 min
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
call .venv\Scripts\activate.bat
python -m celery -A config beat -l INFO
