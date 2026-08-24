# Automatic cuts — strategy and prompts

## Overview

System to analyze podcast/video transcriptions via Grok API (xAI) and suggest viral cuts automatically. The user uploads the source video; the system transcribes (or receives a timestamped transcript), processes in chunks, and returns ranked suggestions.

---

## Output specifications

> The ranges below are **enforced in code**, in `apps/auto_cuts/services/analysis_flow.py`
> (constants at the top of the file) and asserted in
> `apps/auto_cuts/tests/test_create_suggestions_characterization.py`. What the prompt asks
> for and what the backend accepts are two different things — a clip outside the range is
> discarded (below the minimum) or truncated (above the maximum), whatever the prompt said.
> Sections further down this document are older than this one; when they disagree, the
> constants win.

### Short cuts (Reels, TikTok, Shorts)

| Mode | Prompt asks | Backend enforces |
|------|-------------|------------------|
| `viral`, `viral_en`, `viral_translate` | 30–60s | below 30s discarded · above 60s truncated |
| `viral_long`, `viral_long_en` | 90–160s | below 80s discarded unless score > 95 (never below 30s) · above 160s truncated |
| `educational`, `educational_en` | 120–150s | above 150s truncated · no minimum |

Plus a **hard ceiling of 170s (`SHORT_MAX_SEC_HARD`) applied to every short after the
per-mode branches**, whatever the `prompt_version`. The per-mode bands above are the day to
day rule; the hard ceiling is the net for what has no band of its own — an unknown
`prompt_version`, or a new mode whose author forgot to add it to the decision tuples. It
sits above the widest band in use (viral_long, 160s), so it changes no existing mode.

- **Target:** high-impact moments (humor, shock, quotable, controversy, emotion)
- **Platform ceiling:** YouTube classifies a video as a Short up to 3 minutes (180s). The
  educational band was 120–180s until 2026-08-24: a 180.0s cut becomes 180.0x after the
  30fps re-encode and drops out of the Short classification, which was observed in
  production. Every band now keeps at least 10s of headroom.

### Long cuts (YouTube)

| Mode | Prompt asks | Backend enforces |
|------|-------------|------------------|
| viral modes | 8–40 min | below 8 min discarded · above 40 min truncated |
| educational modes | 20–40 min | same 8–40 min band |

- Since **2026-08-24** the band is a single rule for all seven modes (`LONG_CUT_MIN_SEC` /
  `LONG_CUT_MAX_SEC`). It used to be 8–15 min and applied only to the viral modes, which
  left educational long cuts with no ceiling at all and truncated a 25-minute block at 15.
- `duration_minutes` is always derived from the clip timecodes, never from the `duration_min`
  the model reports — the extractor uses the timecodes, so anything else would be a field
  that disagrees with the file on disk.
- **Target:** blocks with viral potential, cohesive narrative, strong title, strong opening hook

---

## Scoring

`virality_score` is an integer **0–100 in every analysis mode**, including the educational
ones (they used 1–10 until 2026-08-24). The backend never rescales what the model returns —
`_normalize_virality_score` only strips a `%` and clamps to 0..100 — so the scale lives in
the prompt and nowhere else. A prompt that introduces a different scale silently produces
scores that cannot be compared with the rest of the table.

> Two exceptions, both deliberate. **Ready-cut metadata** (`READY_CUT_SYSTEM_PROMPT_BASE`)
> still asks for 1–10 and is multiplied by 10 in `_process_ready_cuts_flow`; changing that
> prompt without touching the conversion would scale the score twice. And **suggestions
> written before 2026-08-24 in educational mode** are stored on the old 1–10 scale — there
> was no migration, because the score is only read when the suggestion is created, and old
> rows are never re-sorted or re-filtered.

### Bands

The prompts anchor the score so that a number means the same thing across videos — without
anchors it is only a relative ranking, and the best moment of a bad video scores 90 just
like the best moment of a great one.

| Band | Meaning |
|------|---------|
| 85–100 | Exceptional. Stops the scroll on its own, works with no context from the episode. |
| 70–84 | Good. Stands alone and delivers what the title promises. |
| 50–69 | Average. Only worth publishing if there is nothing better in the video. |
| 0–49 | Weak. Needs outside context, too technical, drags, no hook, or does not travel. |

The prompts state explicitly that candidates below 50 are expected, and forbid raising a
score to fill the requested count — the count instruction sits in the same prompt and pushes
the other way.

### Minimum score filter

`AUTO_CUT_MIN_VIRALITY_SCORE` (default `0` = off) discards any suggestion scoring below it,
before the delivery cap is applied.

- **Inclusive**: with 70, a clip scoring exactly 70 is kept.
- **Missing score is not zero**: a clip with no readable `virality_score` is *not* discarded.
  Absent means "not scored", and treating it as zero would turn a response-format glitch into
  silent loss of good content.
- **Zero surviving cuts is not an error.** A video whose best moment does not clear the bar
  produces no cuts, with a `progress_message` saying so. Turning that into `status="error"`
  would make the factory treat weak content as a system failure.

**How to pick the number.** Turn it on at `0`, run real jobs, and read the
`[FLUXO] Analysis N: X short(s) descartado(s) por nota abaixo de Y` lines to see how many
would fall at each level before committing to one. A starting point of 60 is reasonable;
raise it slowly and watch the stock of AVAILABLE videos in the factory, since a strict bar
plus a weak source library dries up the queue.

The filter only makes sense with the calibration above in the prompts. Against an
un-anchored score, a fixed threshold means nothing.

---

## Chunking strategy

### Why chunking
- Chunks too small → loss of global context and transitions
- Chunks too large → lower precision for short viral moments and higher cost

### Parameters

| Parameter | Value | Reason |
|-----------|-------|--------|
| **Chunk size** | 10–20 min of transcript | ~8,000–18,000 tokens; balance context vs focus |
| **Chunk overlap** | 2–3 min | Avoid cutting moments that span boundaries; preserve transitions |
| **Minimum chunk** | 5 min | Avoid tiny trailing chunks |

### Split rules
1. Split transcript into 10–20 min blocks (by timestamp).
2. Chunk N ends where chunk N+1 starts.
3. Overlap: last 2–3 min of chunk N are the first 2–3 min of chunk N+1.
4. Last chunk may be shorter (e.g. 5 min) if the video does not fill a full block.
5. Keep original timestamps in each chunk for reference.

### Example (45 min video)
- Chunk 1: 00:00 – 00:18 (18 min)
- Chunk 2: 00:15 – 00:33 (18 min, 3 min overlap)
- Chunk 3: 00:30 – 00:45 (15 min, 3 min overlap)

---

## Processing flow

1. **Input:** video or timestamped transcript (MM:SS or HH:MM:SS).
2. **Transcription:** if video, use Whisper (existing) to produce timestamped transcript.
3. **Chunking:** split transcript per strategy above.
4. **Per chunk:** send System + User (chunk) to Grok; receive JSON with suggestions.
5. **Aggregation:** send all suggestions + aggregation prompt; receive final JSON.
6. **Output:** ranked list of short cuts (top 10–15) + 1–3 long cuts, each with:
   - `start` / `end` (timestamps)
   - `title` or `title_suggestion`
   - `reason` (viral rationale)
   - `hook` (short: opening line)
   - `virality_score` (short: 1–10)
   - `duration` or `duration_min`

---

> ### ⚠ As três seções de prompt abaixo são um registro de 2025, não o texto atual
>
> Elas foram escritas antes de os prompts existirem em código e não acompanharam nenhuma
> mudança desde então: falam de uma chamada por chunk (hoje é uma requisição única), de
> agregação num terceiro prompt (não existe), e de `virality_score` 1–10 (hoje é 0–100 em
> todos os modos — ver **Scoring**).
>
> **A fonte da verdade é `apps/auto_cuts/prompts/`**, com o texto congelado por hash em
> `apps/auto_cuts/tests/test_grok_prompts_integridade.py`. Estas seções ficam como registro
> de como o desenho começou.

## Prompt 1: System (fixed)

```
You are a viral editor specializing in podcasts and videos for Reels, TikTok, Shorts, and YouTube. Analyze timestamped transcripts and identify high-engagement segments. Focus on moments that stop the scroll and drive shares and comments.

SHORT VIRAL CRITERIA (15–90 sec, max 3 min) — prioritize top 5–8 per chunk:
- Strong hook in first 3s: shocking question, absurd fact, unexpected humor
- High emotion: surprise, anger, inspiration, controversy, roast
- Quotable lines, meme potential, "mind blown"
- Hot debates, revelations, impactful short stories
- Relatable or controversial
- Satisfying ending: do not cut mid-idea

LONG VIRAL CRITERIA (10–30 min) — YouTube:
- Complete narrative blocks with multiple peaks
- Deep themes, personal stories, valuable explanations
- Natural flow without excessive filler
- Potentially viral title (curious, controversial, clear promise)
- Strong opening hook in first 30 seconds

OUTPUT FORMAT — VALID JSON ONLY, NO EXTRA TEXT BEFORE OR AFTER:

For short cuts:
- start, end: string MM:SS or HH:MM:SS
- duration: number (seconds)
- hook: opening line that grabs attention (first 3s)
- title: suggested title (max 60 chars)
- reason: viral potential rationale
- virality_score: 1–10 (10 = maximum potential)

For long cuts (partial or final):
- start, end: string MM:SS or HH:MM:SS
- duration_min: number (minutes)
- title_suggestion: catchy title (max 100 chars)
- reason: why it could go viral

IMPORTANT: Use ONLY timestamps that appear in the transcript. Do not invent or estimate.
```

---

## Prompt 2: User (per chunk)

```
Transcript chunk (with timestamps):

---
[PASTE CHUNK TEXT WITH TIMESTAMPS HERE]
---

Analyze and suggest:
- 5–8 short viral segments (15–90 sec each, max 3 min) with a strong hook
- 0–2 partial long suggestions (if there is a strong 10+ min block)

Use the system prompt criteria. Focus on scroll-stopping moments that drive shares/comments. For partial long cuts, set segment_type: "start", "middle", or "end" depending on narrative fit.

Reply ONLY with valid JSON:
{
  "short_virals": [
    {
      "start": "MM:SS",
      "end": "MM:SS",
      "duration": 45,
      "hook": "opening line that grabs attention",
      "title": "Suggested title",
      "reason": "viral rationale",
      "virality_score": 8
    }
  ],
  "long_virals_partial": [
    {
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 15,
      "title_suggestion": "Catchy title",
      "reason": "why it could go viral",
      "segment_type": "start|middle|end"
    }
  ]
}
```

---

## Prompt 3: Aggregation (final)

```
Here are all short and partial long viral suggestions from previous chunks:

---
[PASTE ALL JSONs RETURNED PER CHUNK, CONCATENATED]
---

Tasks:

1. RANKED_SHORTS: Rank short_virals by virality_score + real potential. Consider: emotion, quotability, timeliness. Select TOP 10–15. If timestamps overlap, keep the best and discard the other.

2. FINAL_LONG_CUTS: Build 1–3 long cuts (10–30 min) by combining partial blocks with natural overlap and good narrative flow. Suggest a strong title and viral rationale for each.

Reply ONLY with updated JSON:
{
  "ranked_shorts": [
    {
      "rank": 1,
      "start": "MM:SS",
      "end": "MM:SS",
      "duration": 45,
      "hook": "opening line",
      "title": "string",
      "reason": "string",
      "virality_score": 9
    }
  ],
  "final_long_cuts": [
    {
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 18,
      "title_suggestion": "string",
      "reason": "string"
    }
  ]
}

Maximum: 10–15 short cuts, 3 long cuts.
```

---

## Final format for the user

Each item shown on the "Auto Cuts" screen:

| Field | Source | Display |
|-------|--------|---------|
| **Title** | `title` / `title_suggestion` | Suggested cut name |
| **Start** | `start` | e.g. 12:34 |
| **End** | `end` | e.g. 14:22 |
| **Duration** | `duration` / `duration_min` | e.g. 1m 48s or 18m |
| **Hook** | `hook` (short) | Opening line |
| **Rationale** | `reason` | Viral potential |
| **Score** | `virality_score` | 0–100 (see Scoring above) |
| **Rank** | `rank` (short) | Position in top 10–15 |

User actions:
- **Generate cut** → (future) create Cut with start_tc and end_tc
- **Delete** → remove suggestion from list (does not create a cut)

---

## UI flow (Auto Cuts)

1. Sidebar: new item "Auto Cuts"
2. Screen: upload source video (or pick existing source)
3. Button "Generate cuts" → transcription (if needed) + chunking + Grok calls + aggregation
4. During processing: progress (transcribing, analyzing chunk X/Y, aggregating)
5. Result: list of suggestions with title, time range, rationale
6. Per item: "Generate cut" and "Delete"
7. "Generate cut": placeholder for future implementation

---

## Note on prompts

Prompts were refined using Grok’s own suggestions for viral detection scenarios (2025–2026). Main additions: `virality_score`, `hook`, "scroll-stopping" criterion, ranking by emotion/quotability/timeliness, top 10–15 shorts.

---

## Environment variables

- **XAI_API_KEY:** xAI API key (required to generate cuts)
- **GROK_MODEL:** Grok model (optional, default: grok-2-latest)

## Technical notes (future implementation)

- **Grok API:** endpoint https://api.x.ai/v1, model grok-2-latest
- **Timestamp conversion:** MM:SS / HH:MM:SS → seconds for existing cut pipeline
- **Cut integration:** `start_tc`, `end_tc`, `name` (title), `source` (original video)
- **Cache:** store transcript and suggestions to avoid reprocessing
- **Rate limits:** Grok may throttle; consider retry and backoff
