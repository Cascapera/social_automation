# Design: Subtitle system with Whisper

## Overview

Flow: Generate transcript (Whisper) → User edits text → Burn subtitles into video.

---

## 1. Data model

### Option A: Fields on Job (simpler)

```python
# Job model — add:
subtitle_status = CharField(null=True, blank=True)
# null = no subtitles
# "generating" = Whisper running
# "ready_for_edit" = ready to edit
# "approved" = user confirmed (optional)
# "burning" = burning into video
# "burned" = done

subtitle_segments = JSONField(null=True, blank=True)
# [{ "start": 0.0, "end": 2.5, "text": "Hello everyone" }, ...]

subtitle_style = JSONField(null=True, blank=True)
# { "font": "Arial", "size": 24, "color": "#FFFFFF", "outline_color": "#000000", "position": "bottom" }
# Defaults if null
```

### Option B: Separate model JobSubtitle

```python
class JobSubtitle(models.Model):
    job = OneToOneField(Job, on_delete=CASCADE)
    status = CharField()  # generating, ready_for_edit, burning, burned
    segments = JSONField()  # [{start, end, text}, ...]
    created_at = DateTimeField()
    updated_at = DateTimeField()
```

**Recommendation:** Option A (fields on Job) for MVP.

---

## 2. API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/jobs/{id}/generate-subtitles/` | Start subtitle generation. Enqueues Celery task. Returns `{ "status": "generating" }` |
| `GET` | `/api/jobs/{id}/` | Include `subtitle_status` and `subtitle_segments` in response |
| `PATCH` | `/api/jobs/{id}/subtitles/` | Update segments and/or style. Body: `{ "segments": [...], "style": {...} }` |
| `POST` | `/api/jobs/{id}/burn-subtitles/` | Burn subtitles into video. Enqueues Celery. Returns `{ "status": "burning" }` |
| `GET` | `/api/jobs/{id}/download-srt/` | (Optional) Download SRT for preview |

---

## 3. Celery tasks

### Task 1: `generate_subtitles_task(job_id)`

1. Load job and output file (RenderOutput)
2. Extract audio from video (or pass video directly to Whisper)
3. Run faster-whisper (medium model)
4. Convert result to segments `[{start, end, text}, ...]`
5. Save to `job.subtitle_segments`, `job.subtitle_status = "ready_for_edit"`
6. On error: `job.subtitle_status = "error"`, `job.subtitle_error = "..."`

### Task 2: `burn_subtitles_task(job_id)`

1. Load job and `subtitle_segments`
2. Build temporary SRT file
3. FFmpeg: `-vf "subtitles=file.srt"` to burn into video
4. Replace file on RenderOutput (or create new and update)
5. `job.subtitle_status = "burned"`
6. Remove temporary SRT

---

## 4. UI flow (frontend)

### 4.1 Page: Edit Videos (finished videos section)

**Finished job card (DONE status):**

| `subtitle_status` state | Buttons/UI |
|-------------------------|------------|
| `null` or empty | **"Generate subtitles"** button |
| `generating` | "Generating subtitles..." + spinner |
| `ready_for_edit` | **"Edit subtitles"** button |
| `burning` | "Burning subtitles..." + spinner |
| `burned` | Badge "With subtitles" (optional) or no button |
| `error` | Error message + "Retry" button |

### 4.2 Modal/Page: Subtitle editor

**Access:** Click "Edit subtitles" on the job card.

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Edit subtitles - [Job name]                       [X]  │
├─────────────────────────────────────────────────────────┤
│  ▼ Subtitle style                                       │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Font: [Arial        ▼]  Size: [24    ]          │   │
│  │ Text color: [■ #FFFFFF]  Outline: [■ #000000]   │   │
│  │ Position: [Bottom ▼]                            │   │
│  └─────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  Segment 1   [0:00 → 0:03]                              │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Hello everyone, welcome to the channel           │   │
│  └─────────────────────────────────────────────────┘   │
│  ...                                                    │
├─────────────────────────────────────────────────────────┤
│  [Cancel]  [Save changes]  [Burn into video]          │
└─────────────────────────────────────────────────────────┘
```

**Behavior:**
- Each segment: timestamp (read-only) + editable textarea
- "Save changes" → PATCH `/api/jobs/{id}/subtitles/` with edited segments
- "Burn into video" → POST `/api/jobs/{id}/burn-subtitles/` → close modal, show "Burning..."
- Poll job for status (same as processing)

### 4.3 Alternative: Dedicated page

**Route:** `/edit-videos/subtitles/:jobId`

- Same content as modal, full page
- Useful for many segments

**Recommendation:** Modal first; dedicated page if the modal gets heavy.

---

## 5. Full sequential flow

```
1. Job DONE, output_url exists
   └─ User clicks "Generate subtitles"

2. POST /jobs/{id}/generate-subtitles/
   └─ Celery: generate_subtitles_task(job_id)
   └─ Frontend: poll job.subtitle_status

3. subtitle_status = "ready_for_edit"
   └─ "Edit subtitles" button appears

4. User clicks "Edit subtitles"
   └─ Modal opens with segments
   └─ User edits text

5. User clicks "Save changes"
   └─ PATCH /jobs/{id}/subtitles/ { segments: [...] }

6. User clicks "Burn into video"
   └─ POST /jobs/{id}/burn-subtitles/
   └─ Celery: burn_subtitles_task(job_id)
   └─ Frontend: poll job.subtitle_status

7. subtitle_status = "burned"
   └─ Video updated with subtitles
   └─ Download returns subtitled video
```

---

## 6. Dependencies

```
# requirements.txt
faster-whisper>=1.0.0
```

**CUDA:** faster-whisper uses PyTorch with CUDA. RTX 3060 needs `pip install faster-whisper` (pulls deps). Ensure `torch` with CUDA is installed.

---

## 7. Suggested implementation order

1. **Backend:** Model (fields on Job) + migrations
2. **Backend:** Task `generate_subtitles_task` + `generate-subtitles/` endpoint
3. **Backend:** Task `burn_subtitles_task` + `burn-subtitles/` + `PATCH subtitles/`
4. **Backend:** Include `subtitle_status` and `subtitle_segments` in JobSerializer
5. **Frontend:** Buttons in "Finished videos" section
6. **Frontend:** Subtitle editor modal
7. **Frontend:** Polling and loading states
8. **Tests:** End-to-end flow

---

## 8. Subtitle style (editable)

### Parameters supported by FFmpeg (force_style)

| Parameter | Example | Description |
|-----------|---------|-------------|
| FontName | Arial, Helvetica | Font name |
| FontSize | 18, 24, 32 | Size in px |
| PrimaryColour | &H00FFFFFF | Text color (BGR hex) |
| OutlineColour | &H00000000 | Outline color |
| BorderStyle | 1 | 1 = outline + shadow |
| Outline | 2 | Outline width |
| Shadow | 1 | Shadow depth |
| Alignment | 2 | 1=bottom, 2=center, 3=top |
| MarginV | 20 | Vertical margin (px from edge) |

### Edit UI

- **Font:** Dropdown (Arial, Helvetica, Open Sans, Roboto, etc.)
- **Size:** Numeric input (16–48)
- **Text color:** Color picker (hex)
- **Outline color:** Color picker (for readable outline)
- **Position:** Bottom / Center / Top (useful for vertical 9:16)

### Defaults

```json
{
  "font": "Arial",
  "size": 24,
  "color": "#FFFFFF",
  "outline_color": "#000000",
  "outline": 2,
  "position": "bottom"
}
```

### FFmpeg conversion

- Hex color `#FFFFFF` → ASS uses `&H00BBGGRR` (BGR): `#FFFFFF` → `&H00FFFFFF`
- `position`: bottom=2, center=5, top=8 (ASS Alignment values)

---

## 9. Notes

- **Language:** Auto-detect or allow choice (e.g. `language="pt"` for Whisper)
- **Vertical video:** Adjust subtitle position in FFmpeg for 9:16
