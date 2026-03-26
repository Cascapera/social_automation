# Architecture — Social Automation

High-level view for onboarding and technical review. Business detail stays in models and code.

## Layered view

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite) — dashboard, REST + JWT calls      │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS
┌────────────────────────────▼────────────────────────────────┐
│  Django — REST API (/api), admin, OAuth callback URLs        │
│  Authentication: SimpleJWT + session where applicable        │
└──────────────┬──────────────────────────────┬──────────────┘
               │                                │
     ┌─────────▼─────────┐            ┌─────────▼─────────┐
     │  PostgreSQL /     │            │  Redis (broker)  │
     │  SQLite (dev)     │            │  + django-celery │
     └───────────────────┘            │    -results      │
                                      └─────────┬─────────┘
                                                │
                    ┌───────────────────────────┴───────────────────────────┐
                    │  Celery workers (dedicated queues — see ADR-0001)       │
                    │  processing: FFmpeg, Whisper, auto cuts, jobs           │
                    │  publish: scheduling, social APIs, YouTube reconciliation│
                    └─────────────────────────────────────────────────────────┘
```

**File storage:** `MEDIA_ROOT` (e.g. exported videos, cuts, thumbnails). In Docker this is usually a mounted volume.

## Main flow (happy path)

1. **Setup:** user registers via API, creates *Factory*, *Brands*, YouTube credentials / search channels.
2. **Ingestion:** video enters via upload, URL, or *auto-fetch* (yt-dlp + age/view policies on the model).
3. **Heavy work (`processing` queue):**
   - Manual *jobs:* cuts, concatenation, subtitles (Whisper + FFmpeg burn).
   - *Auto cuts:* transcription, LLM analysis, suggestions, cut rendering.
4. **Inventory and scheduling:** ready cuts enter the inventory model; daily *scheduler* (e.g. 19:00) and *beat* create/update `ScheduledPost`.
5. **Publishing (`publish` queue):** scheduled post checks, upload to YouTube / Upload-Post, reconciliation with the YouTube API.

**Celery Beat** (`config/celery.py`) runs periodic tasks: post checks, YouTube reconciliation, daily schedule generation, *auto-fetch* at configured intervals.

## Django apps (responsibility)

| App | Role |
|-----|------|
| `apps.api` | REST viewsets, serializers, HTTP contract |
| `apps.brands` | Factory, Brand, assets, OAuth per brand |
| `apps.mediahub` | `SourceVideo` (editing sources) |
| `apps.cuts` | Cuts derived from source |
| `apps.jobs` | Editing jobs, outputs, schedules, inventory |
| `apps.auto_cuts` | AI pipeline: analysis, suggestions, automatic cuts |
| `apps.social` | Publishing tasks, YouTube integrations, OAuth helpers |

## Out of scope for this document

- Detail of each HTTP *endpoint* — see **[API.md](API.md)** (routes, JWT, custom actions). OpenAPI/Swagger can be added later (e.g. `drf-spectacular`).
- Exact *retry* policies, API limits, and secrets — variables in `.env.example` and task code.

## ADRs (Architecture Decision Records)

Stable, discussable decisions live in [`docs/adr/`](adr/). Start with:

- [0001 — Celery queues `processing` vs `publish`](adr/0001-celery-queues-processing-vs-publish.md)
