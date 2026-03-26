# 🎬 Social Automation — Viral Content Automation Platform

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![Celery](https://img.shields.io/badge/Celery-5.3+-37814A?logo=celery&logoColor=white)](https://celeryproject.org)
[![React](https://img.shields.io/badge/React-18+-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-336791?logo=postgresql&logoColor=white)](https://postgresql.org)
[![CI](https://github.com/Cascapera/social_automation/actions/workflows/ci.yml/badge.svg)](https://github.com/Cascapera/social_automation/actions/workflows/ci.yml)

> **Enterprise-grade production and distribution of viral video for social networks** — from AI transcription to scheduled posting on YouTube, TikTok, Instagram, and X.

---

## 📌 Highlights

| Area | Implementation |
|------|----------------|
| **Architecture** | Modular Django monolith + Celery with dedicated queues (processing × publish) |
| **Scalability** | Separate Celery workers, GPU support (NVENC, Whisper), PostgreSQL with connection pooling |
| **AI/ML** | Whisper (transcription), Grok/OpenAI (viral analysis), CTR-optimized prompts |
| **Integrations** | YouTube Data API v3, multi-account OAuth2, yt-dlp for ingestion |
| **DevOps** | Production-ready Docker Compose, healthchecks, automated migrations |
| **Security** | JWT (SimpleJWT), configured CORS, per-brand credentials, environment variables |
| **Quality** | GitHub Actions (Ruff, `manage.py check`, pytest + coverage, frontend build), Dependabot |

---

## Quality and tests

- **CI**: workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — **Ruff** lint, Django checks, **pytest** with coverage (minimum 70% on included modules: models, `job_actions`, YouTube description, secret encryption, URLs, etc.; excludes heavy integration views/tasks).
- **Test settings**: `social_automation/settings_test.py` + CLI `--ds=...` (see `pyproject.toml`) force **SQLite** in tests even when `DATABASE_URL` is set in the environment.
- **Development**: `pip install -r requirements-dev.txt` then `pytest` / `ruff check .` from the project root.
- **Configuration**: copy [`.env.example`](.env.example) to `.env` and fill in values; **never** commit `.env` or database dumps.

**Language policy:** backend code, operator logs, comments, and technical docs are in **English**. The **React UI** stays in **Portuguese** (`frontend/`). Strings persisted or returned for user-visible errors (e.g. `progress_message`, `last_error`, OAuth hints) may stay in **Portuguese** where they surface in the app.

---

## 🎯 What this project solves

Scaled content production for **video factories** — companies running multiple channels (YouTube, Shorts, TikTok, Instagram, X) from one pipeline:

1. **Ingestion** — Upload, YouTube URL, or automatic fetch from configured channels  
2. **Transcription** — Whisper (faster-whisper) with optional GPU  
3. **AI analysis** — LLM finds viral segments, suggests titles, hooks, and thumbnails  
4. **Editing** — FFmpeg pipeline: cuts, 9:16 reframing, intro/outro, burned subtitles  
5. **Scheduling** — Daily factory scheduler, per-brand slots, timezone-aware  
6. **Publishing** — YouTube (Shorts + long-form), TikTok, Instagram, X via APIs and OAuth  

---

## 🏗️ System architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND (React + Vite)                            │
│                    Dashboard • Jobs • Auto Cuts • Brands                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BACKEND (Django REST + JWT)                                │
│  /api/*  •  /admin/  •  /social/youtube/  •  Media • Migrations              │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
          ┌─────────────────────────────┼─────────────────────────────┐
          ▼                             ▼                             ▼
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│  CELERY WORKER   │         │ CELERY WORKER    │         │   CELERY BEAT     │
│  (processing)    │         │ (publish)        │         │   (scheduler)   │
│  • Jobs FFmpeg   │         │ • Check posts    │         │ • 19h: schedule │
│  • Auto Cuts     │         │ • Upload YT      │         │ • 15min: fetch  │
│  • Whisper       │         │ • Reconcile      │         │ • 1min: posts   │
│  • Subtitles     │         │ • Thumbnails     │         │ • 4h: cleanup   │
└────────┬─────────┘         └────────┬─────────┘         └────────┬─────────┘
         │                            │                            │
         └────────────────────────────┼────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Redis (broker)  │  PostgreSQL  │  Storage (media)  │  FFmpeg  │  APIs       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Technology stack

| Layer | Technologies |
|-------|----------------|
| **Backend** | Django 5.2, Django REST Framework, SimpleJWT, django-celery-results |
| **Queues** | Celery 5.3, Redis 7 |
| **Database** | PostgreSQL 16 (production) / SQLite (dev) |
| **AI** | faster-whisper, OpenAI API, Grok (xAI) |
| **Media** | FFmpeg, yt-dlp, Pillow |
| **Integrations** | Google API (YouTube Data v3), OAuth2 |
| **Frontend** | React 18, Vite, React Router |
| **Infra** | Docker, Docker Compose |

---

## 🚀 Main features

### Auto Cuts (AI)
- Automatic transcription with **Whisper** (CPU/GPU)
- Virality analysis with **LLM** (Grok/OpenAI)
- Short (Shorts) and long (YouTube) cut suggestions
- Auto thumbnails with configurable fonts and colors
- Modes: viral, **viral long** (shorts 90–160s), educational, PT, EN, EN→PT translation
- Vertical reframing: zoom/crop or centered frame

### Editing jobs
- FFmpeg pipeline: cuts, concatenation, transitions (fade, wipe, dissolve)
- Per-brand intro/outro
- Burned subtitles (Whisper + styling)
- **NVENC** support (GPU acceleration)
- Export for multiple platforms

### Factory & brands
- **Factory**: production unit with timezone and scheduling windows
- **Brands**: channels by theme (Business, Psychology, Stories, Controversy, Comedy)
- **Auto-fetch**: automatic fetch from configured YouTube channels
- Policies: minimum video age, minimum views, deduplication

### Publishing
- Daily automatic scheduling (19:00 default)
- YouTube Shorts + long-form, TikTok, Instagram, X
- OAuth per brand/channel
- YouTube reconciliation (real video status)
- Retry with backoff, deduplication by fingerprint

---

## ⚙️ Prerequisites

- **Python 3.11+**
- **FFmpeg** (optional NVENC for GPU)
- **Redis** (for Celery)
- **PostgreSQL** (optional; SQLite for dev)
- **Node.js 18+** (for the frontend)

---

## 🛠️ Quick install

### 1. Clone and virtual environment

```bash
git clone <repo-url>
cd social_automation
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 2. Environment variables

Create a `.env` file at the project root:

```env
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=1
DATABASE_URL=                    # empty = SQLite
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=django-db
OPENAI_API_KEY=sk-...            # transcription / analysis
XAI_API_KEY=xai-...              # Grok (optional)
# YouTube OAuth: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI

# yt-dlp (video download for analysis): if you see "Sign in / not a bot"
# Docker: put the file in secrets/ (gitignored) and use the path inside the container:
# YTDLP_COOKIES_FILE=/app/secrets/youtube_cookies.txt
# Outside Docker (absolute path on host):
# YTDLP_COOKIES_FILE=C:/Users/.../youtube_cookies.txt
# Worker on same machine as Chrome only (not in Docker):
# YTDLP_COOKIES_FROM_BROWSER=chrome
# Export cookies: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies
```

### 3. Database and migrations

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Run with Docker (recommended)

```bash
docker compose up -d
# Open: http://localhost:8000/admin
```

**YouTube cookies (yt-dlp) in Docker:** `docker-compose` mounts the project at `/app`. Create a `secrets/` folder at the repo root (gitignored), export cookies from Chrome (extension **Get cookies.txt LOCALLY** — see [yt-dlp wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)), save as `secrets/youtube_cookies.txt`, and in `.env`:

```env
YTDLP_COOKIES_FILE=/app/secrets/youtube_cookies.txt
```

Restart the worker that downloads video (`docker compose restart celery`). `YTDLP_COOKIES_FROM_BROWSER` is **not** suitable inside the container (no Chrome profile there); always use the file. Refresh `youtube_cookies.txt` when YouTube requires login again.

**“n” / “Only images” / EJS:** YouTube requires [EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS): (1) **`pip install "yt-dlp[default]"`** includes **yt-dlp-ejs**; (2) the Docker image includes **Deno** (default JS runtime for yt-dlp). Rebuild: `docker compose build --no-cache` and `docker compose up -d`. With **cookies**, yt-dlp ignores `android/ios` clients — the code uses `web,mweb,tv_embedded`; adjust with `YTDLP_YOUTUBE_PLAYER_CLIENTS`. Optional: `YTDLP_JS_RUNTIMES=node` if you prefer Node 20+ over Deno.

### 5. Frontend (optional)

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173
```

---

## 📁 Project structure

```
social_automation/
├── apps/
│   ├── api/              # REST API
│   ├── auto_cuts/        # AI: transcription, analysis, suggestions
│   ├── brands/           # Factory, Brand, SearchChannel, OAuth
│   ├── cuts/             # Manual cuts
│   ├── jobs/             # Editing pipeline, ScheduledPost
│   ├── mediahub/         # SourceVideo
│   └── social/           # Publishing, YouTube, scheduling tasks
├── config/               # Celery app and beat_schedule
├── social_automation/    # Settings, URLs
├── frontend/             # React + Vite
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## 🔧 Useful commands

| Command | Description |
|---------|-------------|
| `python manage.py runserver` | Start Django dev server |
| `start_celery.bat` | Processing worker (jobs, auto cuts) |
| `start_celery_publish.bat` | Publish worker (YouTube, Upload Post) |
| `start_celery_beat.bat` | Beat scheduler |
| `celery -A config worker -l INFO -Q processing` | Processing worker (Linux) |
| `celery -A config worker -l INFO -Q publish` | Publish worker (Linux) |
| `celery -A config beat -l INFO` | Beat scheduler |
| `python manage.py run_scheduled_posts_now` | Force immediate posting |
| `python manage.py fix_youtube_posted_status` | Reconcile YouTube status |

---

## 📐 Architecture decisions

Further documentation: **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** (layers and flows), **[`docs/API.md`](docs/API.md)** (REST, JWT, routes and actions), and **[ADRs in `docs/adr/`](docs/adr/)** (recorded decisions, e.g. Celery queues).

- **Separate queues**: `processing` (heavy) and `publish` (light) — prevents transcription/render from blocking scheduling
- **Factory/Brand**: multi-tenant model per business unit
- **OAuth per brand**: isolated credentials per channel, ordered fallback
- **Deduplication**: `ProcessedYoutubeVideo` and `upload_fingerprint` avoid reprocessing and duplicate uploads
- **Idempotency**: `FactoryScheduleRun` per date prevents duplicate daily schedules

---

## 📄 License

Private project. Contact for commercial use.

---

## 👤 Author

Built with a focus on **scalability**, **maintainability**, and **software engineering best practices**.

---

*README aimed at recruiters and technical leads — demonstrates Python backend, distributed architecture, applied AI, and API integrations.*
