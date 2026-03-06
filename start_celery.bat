@echo off
REM Força uso do Python correto (onde Pillow está instalado)
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
REM -P solo: necessário no Windows (prefork causa PermissionError)
python -m celery -A config worker -l INFO -P solo
