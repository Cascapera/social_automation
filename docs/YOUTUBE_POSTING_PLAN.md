# Plan: Automatic posting to YouTube (long-form + Shorts)

## Goal

Implement automatic posting to YouTube for:
- **YouTube Shorts (YT):** vertical videos up to 60s (or 90s per current policy)
- **YouTube long-form (YTB):** long videos (16:9 or 9:16)

Both use the same **YouTube Data API v3** and the same OAuth flow.

---

## Current state

| Item | Status |
|------|--------|
| ScheduledPost | ✅ Exists (job, platforms, scheduled_at, status, title, description, tags, privacy_status) |
| PLATFORM YT, YTB | ✅ Defined on Job |
| Celery Beat | ✅ Configured (check every 1 min) |
| Posting tasks | ✅ check_scheduled_posts_task, post_to_platforms_task |
| BrandSocialAccount | ✅ brand, platform, channel_id, tokens |
| YouTube OAuth | ✅ connect, callback, select-channel |
| YouTube publisher | ✅ Real upload via videos.insert |

---

## Implementation phases

### Phase 1 – Base infrastructure

| # | Task | Description |
|---|------|-------------|
| 1.1 | Celery Beat | Configure `CELERY_BEAT_SCHEDULE` to run every 1 min |
| 1.2 | Task `check_scheduled_posts_task` | Fetch ScheduledPost with status=PENDING and scheduled_at <= now |
| 1.3 | Task `post_to_platforms_task` | Receives scheduled_post_id, iterates platforms, calls publisher |
| 1.4 | Model `BrandSocialAccount` | brand, platform, account_id, account_name, **channel_id** (YouTube), access_token, refresh_token, expires_at |

### Phase 2 – YouTube OAuth (Google)

| # | Task | Description |
|---|------|-------------|
| 2.1 | Google Cloud Project | Create project, enable YouTube Data API v3 |
| 2.2 | OAuth 2.0 credentials | Type "Web application" or "Desktop" (client_id, client_secret) |
| 2.3 | Endpoint `/api/youtube/connect/` | Start OAuth, redirect to Google |
| 2.4 | Callback `/api/youtube/callback/` | Receive code, exchange for tokens, call `channels.list(mine=true)` to list channels |
| 2.5 | Channel selection | User picks channel to link; save `channel_id` on BrandSocialAccount |
| 2.6 | Refresh token | YouTube uses refresh_token; renew access_token when expired |
| 2.7 | .env variables | GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI |

### Phase 3 – YouTube publisher

| # | Task | Description |
|---|------|-------------|
| 3.1 | App `apps/social/` | App for publishers |
| 3.2 | `YouTubePublisher` | Class with `publish(scheduled_post, account, metadata) -> video_id` |
| 3.3 | Upload via `videos.insert` | google-api-python-client + MediaFileUpload |
| 3.4 | YT vs YTB | YT: vertical ≤60s; YTB: any duration. Same API, different metadata |
| 3.5 | Metadata | title, description, tags, categoryId, privacyStatus, madeForKids |
| 3.6 | Native scheduling | publishAt (only with privacyStatus=private) – optional |

### Phase 4 – Integration with existing flow

| # | Task | Description |
|---|------|-------------|
| 4.1 | Video for upload | Job → RenderOutput (final video) or AutoCutCorte (finalized cuts) |
| 4.2 | ScheduledPost → Job | ScheduledPost already has FK to Job; Job has target_platforms |
| 4.3 | Choose account | ScheduledPost or Job: which BrandSocialAccount (per brand) |
| 4.4 | Metadata fields | Add title, description, tags on Job or ScheduledPost |
| 4.5 | Auto-cuts | Allow scheduling posts for finalized cuts (AutoCutCorte) |

### Phase 5 – Frontend

| # | Task | Description |
|---|------|-------------|
| 5.1 | "Connected accounts" screen | Per brand: list accounts, "Connect YouTube" button |
| 5.2 | OAuth flow | Button → Google redirect → callback → success/error |
| 5.3 | Scheduling | When creating ScheduledPost: choose YouTube account (if any) |
| 5.4 | Metadata | title, description, tags on scheduling form |
| 5.5 | Auto cuts | "Schedule post" on finalized cuts |

---

## Data flow

```
┌──────────────────────────────────────────────────────────────────┐
│  User schedules post                                              │
│  → ScheduledPost(job=X, platforms=["YT"], scheduled_at=...)       │
│  → Or: schedule AutoCut cut (new flow)                            │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Celery Beat (every 1 min)                                       │
│  → check_scheduled_posts_task()                                  │
│  → Filter: status=PENDING, scheduled_at <= now                  │
│  → For each: post_to_platforms_task.delay(scheduled_post_id)     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  post_to_platforms_task                                          │
│  → For each platform in ["YT", "YTB"]:                           │
│  → BrandSocialAccount.objects.get(brand=..., platform=platform)  │
│  → YouTubePublisher.publish(scheduled_post, account, metadata)   │
│  → Update status=DONE or FAILED, posted_at                       │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  YouTubePublisher.publish()                                      │
│  → Get video: Job.render_outputs or AutoCutCorte.file            │
│  → Refresh token if expired                                      │
│  → videos.insert (snippet, status, media)                        │
│  → Returns video_id                                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Python dependencies

```
google-auth>=2.0.0
google-auth-oauthlib>=1.0.0
google-api-python-client>=2.0.0
```

---

## YT (Shorts) vs YTB (long-form)

| Aspect | YouTube Shorts (YT) | YouTube long-form (YTB) |
|--------|---------------------|-------------------------|
| Duration | ≤ 60s (or 90s) | No limit |
| Format | Vertical 9:16 | Any |
| API | Same (videos.insert) | Same |
| Metadata | title with #Shorts helps discovery | longer title, description |
| Thumbnail | Optional | Recommended |

**Implementation:** Single `YouTubePublisher`; difference is metadata and validation (duration/format).

---

## Suggested sprint order

| Sprint | Items | Deliverable |
|--------|-------|-------------|
| 1 | 1.1–1.4 | Beat running, account model, task stub |
| 2 | 2.1–2.6 | OAuth working, account linked per brand |
| 3 | 3.1–3.6 | Real YouTube publisher, upload working |
| 4 | 4.1–4.5 | Job → video integration, metadata on ScheduledPost |
| 5 | 5.1–5.5 | Frontend: connect account, schedule, post |

---

## Multiple channels on the same Gmail

A Google user can have several YouTube channels (including brand accounts). To target the right channel:

1. **After OAuth**: Call `channels.list(part="snippet", mine=true)` — returns all channels for the authenticated user.
2. **Channel list**: Each item has `id` (channel_id) and `snippet.title` (channel name).
3. **Link by channel**: Each `BrandSocialAccount` stores a specific `channel_id`.
4. **One connection = one channel:** One OAuth connection = one channel. If a brand uses 3 channels, create 3 BrandSocialAccount rows (same Gmail access_token, different channel_id).
5. **When scheduling:** User picks which channel (which BrandSocialAccount) to use.

**Model:**
```python
# BrandSocialAccount
channel_id = models.CharField(max_length=64, blank=True)  # YouTube: UCxxxxxx
account_name = models.CharField(...)  # Display: "Main channel", "EN channel"
```

**UI flow:**
1. Click "Connect YouTube" → OAuth
2. After callback: show list of returned channels
3. User selects "Link channel X to brand Y"
4. Save BrandSocialAccount(brand=Y, platform=YT, channel_id=UCxxx, account_name="Channel X")
5. To add another channel: repeat OAuth (reuse tokens) → pick another channel

---

## Caveats

1. **YouTube API quota:** 10,000 units/day; `videos.insert` = 1600 units. ~6 uploads/day per project.
2. **Tokens:** Store securely; consider encryption in production.
3. **Video file:** Direct file upload (no public URL required).
4. **Auto-cuts:** ScheduledPost is currently per Job; may need `ScheduledPost(analysis, corte, ...)` or model extension.
5. **Shorts 60s vs 90s:** YouTube extended to 90s; validate current limit in docs.

---

## Configuration (.env)

Add to `.env` for YouTube OAuth:

```
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret
YOUTUBE_REDIRECT_URI=http://localhost:8000/api/youtube/callback/
FRONTEND_URL=http://localhost:5173
```

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable **YouTube Data API v3**
3. Create OAuth 2.0 credentials (type "Web application")
4. Under "Authorized redirect URIs", add `http://localhost:8000/api/youtube/callback/`

## Running Celery Beat

For scheduled posts, start Beat in addition to the worker:

```bash
# Terminal 1: Worker
python -m celery -A config worker -l INFO -P solo

# Terminal 2: Beat (schedule check every 1 min)
python -m celery -A config beat -l INFO
```

Or use `start_celery_beat.bat` on Windows.
