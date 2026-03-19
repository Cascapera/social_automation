@echo off
REM Workers separados: processing (jobs) e publish (postagem)
REM Execute ESTE arquivo para jobs + INICIE OUTRA JANELA com start_celery_publish.bat
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
REM -P solo: necessário no Windows (prefork causa PermissionError)
python -m celery -A config worker -l INFO -P solo -Q processing -n processing@%%h
