@echo off
REM Worker de publicação (YouTube, Upload Post, etc). Rode em janela separada.
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
REM --heartbeat-interval=300: evita "missed heartbeat" em uploads longos (Upload Post + YouTube)
python -m celery -A config worker -l INFO -P solo -Q publish -n publish@%%h --heartbeat-interval=300
