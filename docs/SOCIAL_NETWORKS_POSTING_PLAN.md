# Plan: Social networks integration and scheduling

## Current state

- **ScheduledPost** exists: stores job, platforms (IG, TT, YT, YTB), `scheduled_at`, status
- **API** can create schedules via `POST /api/scheduled-posts/`
- **Frontend** has scheduling flow (Agendamento.jsx, NovoVideo.jsx, Dashboard.jsx)
- **Gap:** no Celery task executes posting; schedules stay in the DB only

---

## Proposed architecture

### 1. Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Celery Beat (every 1 min)                                      │
│  → Task: check_scheduled_posts_task                             │
│  → Fetch ScheduledPost with status=PENDING and scheduled_at <= now │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Task: post_to_platforms_task(scheduled_post_id)                 │
│  → For each platform in scheduled_post.platforms                 │
│  → Call matching publisher (IG, TT, YT, YTB)                      │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Publishers (apps/social/publishers/)                            │
│  - InstagramPublisher (Reels)                                    │
│  - TikTokPublisher                                               │
│  - YouTubeShortsPublisher                                        │
│  - YouTubePublisher (long-form)                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2. Credential storage

**Option A – Per brand (recommended)**  
Each brand has connected accounts. A job belongs to a brand → uses that brand’s credentials.

- New model: `BrandSocialAccount` (brand, platform, access_token, refresh_token, expires_at, extra_data)
- Flow: user connects account in brand panel → OAuth → save tokens

**Option B – Per user**  
Each user connects their accounts. User’s jobs use their credentials.

- Model: `UserSocialAccount` (user, platform, tokens...)
- Simpler for personal use; less flexible for multiple brands

**Recommendation:** Option A (per brand), since the system is multi-brand.

---

## Requirements per platform

### Instagram Reels (IG)

| Item | Detail |
|------|--------|
| **API** | Instagram Graph API (Meta) |
| **Requirements** | Business/Creator account, Meta App, approved permissions |
| **OAuth** | Facebook Login for Business |
| **Permissions** | `instagram_content_publish`, `instagram_business_basic` |
| **Upload** | Public video URL OR resumable upload |
| **Limits** | ~30 posts/day via API |
| **Complexity** | High (Meta approval, Business account) |

### TikTok (TT)

| Item | Detail |
|------|--------|
| **API** | TikTok Content Posting API |
| **Requirements** | TikTok for Developers, approved app |
| **OAuth** | TikTok Login Kit |
| **Upload** | Direct upload or URL |
| **Complexity** | High (TikTok approval, evolving docs) |

### YouTube Shorts (YT)

| Item | Detail |
|------|--------|
| **API** | YouTube Data API v3 |
| **Requirements** | Google Cloud Project, OAuth 2.0 |
| **OAuth** | Google OAuth (Installed App or Web) |
| **Upload** | `videos.insert` with `MediaFileUpload` |
| **Shorts** | Same endpoint; Shorts = vertical ≤ 60s |
| **Complexity** | Medium (well documented) |

### YouTube (YTB) – long-form

| Item | Detail |
|------|--------|
| **API** | YouTube Data API v3 (same as Shorts) |
| **Difference** | No 60s limit; different metadata |
| **Complexity** | Medium |

---

## Implementation strategy

### Phase 1 – Infrastructure (base)

1. **Celery Beat**
   - Configure `CELERY_BEAT_SCHEDULE` to run `check_scheduled_posts_task` every 1 min
   - Task fetches `ScheduledPost` with `status=PENDING` and `scheduled_at <= now`
   - For each: enqueue `post_to_platforms_task`

2. **Credentials model**
   - `BrandSocialAccount`: brand, platform (IG/TT/YT/YTB), tokens, expires_at
   - Migrations

3. **Publisher structure**
   - `apps/social/` (app)
   - Base interface: `publish(job, account, caption?) -> result`
   - One publisher per platform (stub first)

### Phase 2 – YouTube first

- More stable, documented API
- OAuth with `google-auth-oauthlib` and `google-api-python-client`
- “Connect YouTube” screen per brand
- Real publisher for YT and YTB

### Phase 3 – Instagram

- Meta for Developers: create app, configure Instagram API
- OAuth with `requests` or platform-specific lib
- Publisher for Reels
- Note: video must be publicly URL or resumable upload

### Phase 4 – TikTok

- TikTok for Developers
- Publisher when API is stable enough
- Can defer if API is too restrictive

---

## Video upload flow

1. **Public URL**  
   - Serve video at reachable URL (e.g. `https://yourdomain.com/media/exports/job_X.mp4`)  
   - Instagram accepts URL; YouTube uploads file directly

2. **Direct upload**  
   - YouTube: read file from disk and send via `MediaFileUpload`  
   - Instagram: resumable upload or URL  
   - TikTok: per API docs

3. **Caption**  
   - Add `caption` (or `description`) on `Job` or `ScheduledPost`  
   - Each platform may have different rules (hashtags, character limits)

---

## Suggested data model

```python
# BrandSocialAccount (new)
class BrandSocialAccount(models.Model):
    PLATFORM = [("IG", "Instagram"), ("TT", "TikTok"), ("YT", "YouTube Shorts"), ("YTB", "YouTube")]
    brand = models.ForeignKey(Brand, ...)
    platform = models.CharField(choices=PLATFORM)
    account_id = models.CharField()      # Platform ID
    account_name = models.CharField()    # Display name
    access_token = models.TextField()    # Encrypted in production
    refresh_token = models.TextField(null=True)
    token_expires_at = models.DateTimeField(null=True)
    extra_data = models.JSONField(default=dict)  # platform-specific
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

```python
# ScheduledPost (adjustments)
# Add: caption, per_social_account (optional FK to pick account)
```

---

## Suggested implementation order

| # | Task | Effort |
|---|------|--------|
| 1 | Celery Beat + `check_scheduled_posts_task` | Low |
| 2 | `BrandSocialAccount` model + migrations | Low |
| 3 | API: connect/disconnect account (OAuth flow) | Medium |
| 4 | YouTube publisher (YT + YTB) | Medium |
| 5 | Frontend: “Connected accounts” per brand | Medium |
| 6 | Caption field on Job/ScheduledPost | Low |
| 7 | Instagram publisher | High |
| 8 | TikTok publisher | High |

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Slow approval (Meta, TikTok) | Start with YouTube; IG/TT in parallel |
| Token expiry | Auto refresh; notify user on failure |
| Rate limits | Queue with backoff; respect per-platform limits |
| Video not reachable (URL) | Ensure HTTPS, public URL |
| Multiple accounts per platform | `BrandSocialAccount` supports many; user picks when scheduling |

---

## Next step

Choose implementation start:

1. **YouTube** (simpler, good docs)  
2. **Instagram** (more demand, more bureaucracy)  
3. **Both in parallel** (YouTube first, then Instagram)

Suggestion: start with **Phase 1 + YouTube** for an end-to-end flow, then add Instagram and TikTok.
