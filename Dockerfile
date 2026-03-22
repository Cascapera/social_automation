FROM python:3.11-slim

# Deno: runtime JS recomendado pelo yt-dlp para EJS (desafio "n" do YouTube).
# PyPI: instalar também yt-dlp[default] (scripts yt-dlp-ejs). Ver https://github.com/yt-dlp/yt-dlp/wiki/EJS
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    unzip \
    && curl -fsSL https://deno.land/install.sh | sh \
    && install -m 755 /root/.deno/bin/deno /usr/local/bin/deno \
    && rm -rf /root/.deno \
    && rm -rf /var/lib/apt/lists/* \
    && deno --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=social_automation.settings

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
