# 🎬 Social Automation (AI Video Automation Platform)

An AI-powered distributed system that processes thousands of minutes of video daily, automatically generating and publishing content across multiple platforms.

Designed for content factories managing multiple brands and high-volume video pipelines.

---

## 📊 Real-world usage

- Processing **6000+ minutes of video per day**
- Generating **300+ short-form videos daily**
- Publishing **20+ long-form videos per day**
- Managing **40+ social media accounts**
- Fully automated pipeline from ingestion → processing → publishing

---

## 🚀 Overview

This platform automates the entire lifecycle of video content production using AI and distributed processing.

It transforms long-form content into optimized short and long videos, ready for publishing across platforms.

### End-to-end pipeline:

1. Video ingestion (upload, YouTube URLs, automated channel monitoring)
2. Transcription using Whisper (CPU/GPU)
3. AI analysis for viral segment detection (LLMs)
4. Clip generation and content structuring
5. Video processing via FFmpeg (cuts, subtitles, formatting)
6. Scheduling (timezone-aware)
7. Multi-platform publishing (YouTube, TikTok, Instagram, X)

---

## 💡 Problem

Managing multiple content channels at scale requires manual editing, scheduling, and publishing workflows that do not scale.

---

## ✅ Solution

This platform automates the entire pipeline using AI and distributed systems, enabling high-volume content production with minimal manual intervention.

---

## 🧠 Key Features

### AI-Powered Content Generation
- Automatic transcription (Whisper / faster-whisper)
- LLM-based viral segment detection (OpenAI / Grok)
- Title, hook, and thumbnail suggestions
- Multi-language support (PT / EN)

### Video Processing Pipeline
- Automated clipping and concatenation
- Vertical reframing (9:16)
- Subtitle rendering (burned-in)
- GPU acceleration (NVENC)

### Multi-Platform Publishing
- YouTube (Shorts + long-form)
- TikTok
- Instagram
- X (Twitter)

### Multi-Tenant Architecture
- Factories (production units)
- Brands (channels per niche)
- OAuth per brand (isolated credentials)

---

## 🏗️ Architecture

- Modular Django monolith (API + domain logic)
- Celery-based distributed processing
- Queue isolation:
  - `processing` → CPU/GPU-intensive tasks (Whisper, FFmpeg)
  - `publish` → I/O-bound tasks (API uploads)
- Celery Beat for scheduling
- Redis as message broker
- PostgreSQL as primary database

### Design Decisions

- Queue isolation prevents heavy processing from blocking publishing
- Idempotent scheduling avoids duplicate posts
- Deduplication prevents reprocessing of content
- Multi-tenant structure supports scalable content factories

---

## ⚙️ Tech Stack

**Backend**
- Django 5.2
- Django REST Framework
- SimpleJWT

**Async / Processing**
- Celery 5.3
- Redis 7

**Database**
- PostgreSQL 16

**AI / ML**
- faster-whisper
- OpenAI API
- Grok (xAI)

**Media**
- FFmpeg
- yt-dlp

**Frontend**
- React 18 + Vite

**Infra**
- Docker / Docker Compose

---

## 🔁 Processing Pipeline

1. Ingestion
2. Transcription (Whisper)
3. AI analysis (LLM)
4. Clip generation
5. Video rendering (FFmpeg)
6. Scheduling
7. Publishing

---

## 🧠 Engineering Challenges

- Handling CPU/GPU-intensive workloads (Whisper, FFmpeg)
- Preventing queue starvation with task isolation
- Ensuring idempotent scheduling and deduplication
- Managing OAuth credentials across multiple brands
- Scaling video processing pipelines across multiple accounts

---

## 🔍 Observability

Structured **JSON** logs (`log_event` in `apps/jobs/logging_utils.py`), **correlation IDs** across Celery task flows, and event-style records for **scheduling**, **transcription**, **render**, **publish**, and **YouTube reconciliation**.

**Metrics:** Prometheus counters and histograms (`prometheus_client`, `GET /metrics/`) cover transcription, render, publish, and reconciliation. Transcription and render include a **`workload_type`** label (`cpu` / `gpu`) where applicable (Whisper and NVENC-related paths).

**Multiprocess:** Task metrics are updated inside Celery worker processes; **`PROMETHEUS_MULTIPROC_DIR`** must reference a **directory shared** by the Django web process and workers so `/metrics/` can aggregate them (prometheus_client multiprocess mode). Docker Compose in this repo mounts a named volume and sets the variable—see [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

**Validation:** Logs and reconciliation metrics have been checked in running environments. Publish metrics are implemented but not yet validated end-to-end in live runs, to avoid **unnecessary YouTube API quota** during testing.

Further detail: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

---

## 🧪 Testing & Quality

- Pytest with coverage (~70%+ core modules)
- Ruff for linting
- GitHub Actions CI pipeline
- Isolated test environment (SQLite)

---

## 🚧 Future Improvements

- Vector database for semantic search (RAG)
- AI agents for decision-making (content strategy)
- Advanced observability (tracing + metrics dashboards)
- Kubernetes-based deployment

---

## 🔐 Security

- JWT authentication
- OAuth2 per brand (isolated credentials)
- Environment-based configuration
- No sensitive data stored in code

---

## 📄 License

This repository represents a real-world production system currently in active use.

The source code is shared for demonstration and educational purposes only.  
Commercial use, redistribution, or deployment of this code is not permitted without explicit authorization.

---

## 👤 Author

Built with focus on scalability, reliability, and real-world AI system design.
