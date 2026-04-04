# Adjustable posting data per platform

Summary of fields we can configure per network when publishing a video.

---

## Comparative overview

| Field | Instagram Reels | TikTok | YouTube Shorts | YouTube (long-form) |
|-------|-----------------|--------|----------------|---------------------|
| **Title** | ❌ | ✅ (caption) | ✅ | ✅ |
| **Description** | ❌ | ❌ (uses caption) | ✅ | ✅ |
| **Caption** | ✅ | ✅ (title = caption) | — | — |
| **Tags/Hashtags** | (in caption) | (in caption) | ✅ | ✅ |
| **Cover/Thumbnail** | ✅ (cover_url) | ✅ (timestamp) | ✅ | ✅ |
| **Privacy** | ❌ (public) | ✅ | ✅ | ✅ |
| **Location** | ✅ (location_id) | ❌ | ❌ | ❌ |
| **Duet/Stitch** | — | ✅ (disable) | — | — |
| **Comments** | — | ✅ (disable) | — | ✅ |
| **Category** | — | — | ✅ | ✅ |
| **Schedule publish** | ❌ | ❌ | ✅ (publishAt) | ✅ |
| **Made for kids** | — | — | ✅ | ✅ |
| **Paid content** | — | ✅ (brand_content) | — | — |

---

## Instagram Reels (IG)

| Field | Type | Required | Limit | Notes |
|-------|------|----------|-------|-------|
| **caption** | string | No | — | Reel caption. Hashtags and @ in text |
| **cover_url** | URL | No | — | Public image URL for cover (Reels tab) |
| **location_id** | string | No | — | Place ID (Facebook Place) |
| **media_type** | enum | Yes | — | "REELS" |
| **video_url** | URL | Yes | — | Public video URL (MP4) |

**Not available:** separate title, description, tags, privacy (API is usually public), scheduling.

---

## TikTok (TT)

| Field | Type | Required | Limit | Notes |
|-------|------|----------|-------|-------|
| **title** | string | No | 2200 chars (UTF-16) | Caption. # and @ detected |
| **privacy_level** | enum | Yes | — | PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR, SELF_ONLY |
| **video_cover_timestamp_ms** | int | No | — | Frame (ms) used as cover |
| **disable_duet** | bool | No | — | Block Duets |
| **disable_stitch** | bool | No | — | Block Stitches |
| **disable_comment** | bool | No | — | Block comments |
| **brand_content_toggle** | bool | Yes | — | true = paid partnership |
| **brand_organic_toggle** | bool | No | — | true = promoting own business |
| **is_aigc** | bool | No | — | true = AI-generated content |

**Not available:** separate description (everything in title), separate tags, category, scheduling.

---

## YouTube Shorts (YT) and YouTube (YTB)

Both use the same API (`videos.insert`). Shorts = vertical video ≤ 60s.

### snippet (main metadata)

| Field | Type | Required | Limit | Notes |
|-------|------|----------|-------|-------|
| **title** | string | Yes | 100 chars | Video title |
| **description** | string | No | 5000 chars | Description |
| **tags** | array | No | 500 chars total | Tag list |
| **categoryId** | string | Yes | — | e.g. "22" (People & Blogs), "24" (Entertainment) |
| **defaultLanguage** | string | No | — | Language code (e.g. "pt") |
| **defaultAudioLanguage** | string | No | — | Audio language |

### status

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| **privacyStatus** | enum | Yes | public, private, unlisted |
| **publishAt** | datetime | No | Schedule (only with privacyStatus=private) |
| **embeddable** | bool | No | Allow embed |
| **publicStatsViewable** | bool | No | Public stats |
| **madeForKids** | bool | Yes | Made for kids |
| **selfDeclaredMadeForKids** | bool | No | Self-declaration |

### contentDetails (optional)

| Field | Type | Notes |
|-------|------|-------|
| **caption** | bool | Captions available |
| **contentRating** | object | Age rating by country |

### Thumbnail

- Separate upload via `thumbnails.set` after video
- Or YouTube auto-generates if none sent

---

## Unified model proposal in the system

So the user edits once and applies everywhere (with mapping):

```python
# Common fields (ScheduledPost or Job)
caption = "Main caption"           # → IG caption, TT title, YT description (or part)
title = "Video title"               # → YT title, TT title (if differentiated)
description = "Long description"    # → YT description
tags = ["tag1", "tag2"]             # → YT tags, hashtags in caption for IG/TT
hashtags = "#fyp #viral"            # → IG/TT (in caption)

# Per-platform (override or specific)
cover_timestamp_ms = 1000           # TT: cover frame
cover_image_url = "https://..."     # IG: cover URL
privacy = "public"                  # TT, YT (IG often public)
disable_comments = False            # TT
disable_duet = False                # TT
disable_stitch = False              # TT
category_id = "22"                  # YT
made_for_kids = False               # YT
publish_at = "2025-02-20T14:00:00Z" # YT (schedule)
location_id = None                  # IG
brand_content = False               # TT (paid partnership)
```

### Mapping strategy

1. **Single caption** → IG caption, TT title (with hashtags), start of YT description
2. **Title** → YT title; TT can use title or caption
3. **Description** → YT description (can include caption + extra text)
4. **Tags** → YT tags; for IG/TT user puts hashtags in caption
5. **Specific fields** → form with tabs or per-platform sections

---

## Frontend recommendation

**Option A – Simple unified form**
- Caption (required)
- Title (for YT)
- Description (for YT, optional)
- Hashtags (separate field, concatenated into caption for IG/TT)
- Privacy (where applicable)
- Checkboxes: disable comments, Duet, Stitch (TikTok)

**Option B – Tabbed form per platform**
- Tab "General": caption, title, description
- Tab "Instagram": cover, location
- Tab "TikTok": privacy, cover (timestamp), Duet/Stitch/comments
- Tab "YouTube": tags, category, privacy, made for kids, schedule

**Option C – Hybrid**
- Common fields at top (caption, title, description, tags)
- Expandable "Per-network options" with specific overrides

Suggestion: start with **Option A** and evolve toward **Option C** as needed.
