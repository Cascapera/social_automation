# Automatic cuts — strategy and prompts

## Overview

System to analyze podcast/video transcriptions via Grok API (xAI) and suggest viral cuts automatically. The user uploads the source video; the system transcribes (or receives a timestamped transcript), processes in chunks, and returns ranked suggestions.

---

## Output specifications

### Short cuts (Reels, TikTok, Shorts)
- **Ideal duration:** 15–90 seconds
- **Maximum:** 3 minutes
- **Target:** high-impact moments (humor, shock, quotable, controversy, emotion)

### Long cuts (YouTube)
- **Ideal duration:** 10–30 minutes
- **Target:** blocks with viral potential, cohesive narrative, strong title, strong opening hook

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
| **Score** | `virality_score` (short) | 1–10 |
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
