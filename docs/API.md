# HTTP API — Social Automation

Reference for integrations and onboarding. **Canonical contract:** code in `apps/api/` (viewsets, serializers). This document summarizes routes and rules; exact JSON fields are in the serializers.

**Base prefix:** `{ORIGIN}/api/`  
E.g. development `http://127.0.0.1:8000/api/`, production per `ALLOWED_HOSTS` and HTTPS.

**Format:** `Content-Type: application/json` (except multipart *uploads* noted below).

---

## Authentication

The API uses **JWT** (SimpleJWT), by default with `IsAuthenticated` on viewsets.

| Method | Path | Body / notes |
|--------|------|----------------|
| `POST` | `/api/auth/token/` | `{"username": "...", "password": "..."}` → `access`, `refresh` |
| `POST` | `/api/auth/token/refresh/` | `{"refresh": "..."}` → new `access` |
| `POST` | `/api/register/` | Public registration: `username`, `password` (min. 8), optional `email` |

**Header on protected routes:**

```http
Authorization: Bearer <access>
```

---

## YouTube OAuth (callbacks)

Included in `apps.api.urls` under `/api/youtube/`:

| Path | Description |
|------|-------------|
| `GET/POST` | `/api/youtube/connect/` | Brand OAuth start |
| `GET` | `/api/youtube/callback/` | OAuth callback |
| … | `/api/youtube/factory-check-connect/`, `factory-check-callback/`, `pending-channels/`, `select-channel/` | *Factory check* flows and pending channels |

Parameter detail: `apps/social/views.py`.

---

## REST resources (router)

`DefaultRouter` exposes list and detail. Convention:

- `GET /api/<resource>/` — list  
- `POST /api/<resource>/` — create (where allowed)  
- `GET /api/<resource>/{id}/` — detail  
- `PATCH /api/<resource>/{id}/` — partial update  
- `PUT` — only where the viewset does not restrict methods  
- `DELETE /api/<resource>/{id}/` — remove (where allowed)

### Resource table (`apps/api/urls.py`)

| Resource prefix | ViewSet | Notes |
|-----------------|---------|--------|
| `register` | Registration | **POST** only (user creation) |
| `factories` | Factory | **No DELETE**; GET, POST, PATCH |
| `search-channels` | SearchChannel | Common filter: `?factory=` |
| `brands` | Brand | `?factory=` |
| `brand-assets` | BrandAsset | Multipart upload for files |
| `social-accounts` | BrandSocialAccount | **GET, DELETE** only |
| `brand-youtube-credentials` | BrandYouTubeCredential | `?brand=` |
| `sources` | SourceVideo | User source video |
| `cuts` | Cut | Cuts; see `upload` and *bulk* actions on `create` |
| `jobs` | Job | Render jobs; filters `?brand=`, `?archived=` |
| `scheduled-posts` | ScheduledPost | Schedules |
| `video-inventory` | VideoInventoryItem | **Read-only** + actions below |
| `factory-schedules` | FactoryPostingSchedule | **Read-only** |
| `posted-videos` | PostedVideoLog | **Read-only** |
| `auto-cuts` | AutoCutAnalysis | **No PUT/PATCH** on main resource; GET, POST, DELETE; custom `create` |
| `auto-cut-suggestions` | AutoCutSuggestion | Thin viewset: see actions |
| `auto-cut-cortes` | AutoCutCorte | GET, PATCH, POST, DELETE (no PUT by default) |

---

## Custom actions (`@action`)

Routes under `/api/<resource>/{id}/` (or `detail=False` at resource root). Methods as indicated.

### Factory (`/api/factories/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `.../{id}/trigger-immediate-schedule/` | Immediate schedule; optional body `target_date`, `brand_id` |
| `GET` | `.../{id}/youtube-check-connect-url/` | OAuth URL for *YOUTUBE_CHECK_* credentials |

### Brand (`/api/brands/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `.../{id}/trigger-immediate-schedule/` | Same scheduling per brand |
| `GET` | `.../{id}/social_accounts/` | List social accounts |
| `GET` | `.../{id}/youtube_connect_url/` | YouTube OAuth URL (optional `?youtube_credential_id=`) |
| `PATCH` | `.../{id}/youtube-description/` | `youtube_description_extra`, `youtube_made_for_kids` |

### Source (`/api/sources/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `.../{id}/extract_cuts/` | Body `cuts: [{start_tc, end_tc, name?, format?}]` — extracts cuts and removes source |

### Cuts (`/api/cuts/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `/api/cuts/upload/` | Multipart: upload ready cut |

### Jobs (`/api/jobs/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `/api/jobs/upload/` | Multipart: ready video → job DONE with output |
| `GET` | `.../{id}/download/` | Download exported file |
| `POST` | `.../{id}/generate-subtitles/` | Starts Whisper |
| `PATCH` | `.../{id}/subtitles/` | Edit `segments` / `style` |
| `POST` | `.../{id}/burn-subtitles/` | Burn subtitles |
| `POST` | `.../{id}/run/` | Enqueues `process_job` (Celery) |

### Scheduled posts (`/api/scheduled-posts/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `.../{id}/reschedule/` | Reschedule failures (`scheduled_at`) |
| `POST` | `.../{id}/remove-awaiting/` | Remove schedule + linked inventory |

### Video inventory (`/api/video-inventory/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `.../{id}/remove-awaiting/` | Remove awaiting item + media |
| `POST` | `.../{id}/retry-posting/` | Re-enable posting (optional `scheduled_at`) |
| `GET` | `.../{id}/download-media/` | ZIP with video, thumb, title/description text |
| `POST` | `.../{id}/mark-posted/` | Mark as posted manually |

Useful list filters: `?factory=`, `?brand=`, `?status=`, `?video_type=SHORT|LONG`.

### Auto cuts — analysis (`/api/auto-cuts/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `/api/auto-cuts/upload-ready-cuts/` | Multipart: multiple ready videos (`files`, `brand`, `name`, …) |
| `POST` | `/api/auto-cuts/reset-stuck/` | Mark stuck analyses as error |
| `POST` | `/api/auto-cuts/delete-stuck/` | Remove stuck jobs and files |
| `POST` | `.../{id}/finalizar/` | Finalize cuts (body with subtitle, vertical, overlay options, …) |
| `POST` | `.../{id}/bulk-schedule/` | Schedule multiple cuts in a time window |

### Auto cut suggestions (`/api/auto-cut-suggestions/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `DELETE` | `.../{id}/` | Remove suggestion |
| `POST` | `.../{id}/create-cut/` | Informational response (automatic generation still limited) |

### Auto cut cortes (`/api/auto-cut-cortes/`)

| Method | Suffix | Description |
|--------|--------|------------|
| `POST` | `.../{id}/schedule/` | Schedule a finalized cut |

---

## Errors and status codes

- **`401` / `403`:** not authenticated or no permission on the resource.  
- **`400`:** validation (missing fields, invalid state for the action).  
- **`404`:** resource missing or file not on disk.  
- **`500` / `503`:** internal error or service unavailable (e.g. OAuth not configured in `.env`).

Error bodies usually include an `"error"` key with a human-readable message.

---

## Evolution: machine-readable docs

For **OpenAPI 3** (Swagger UI / Redoc) generated from serializers, the typical Django REST Framework step is adding **`drf-spectacular`** (or equivalent) and exposing `/api/schema/`. It is not wired in this repo to keep dependencies minimal; it can be a future improvement when the team wants versioned contracts for external clients.

---

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system view and flows  
- [Main README](../README.md) — environment variables and local run  
