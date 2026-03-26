# Technical plan: Multichannel factory (Shorts + long-form)

## Goal

Implement an operational layer called `Factory` to manage multiple `Brands` (channels) with:

- automated cut generation by category;
- per-brand video bank (shorts and long-form);
- smart daily scheduling by window, interval, and limits;
- publishing with retries and debug logs;
- posted video history for future analytics.

This document is **planning only**, not implementation.

---

## Business rules (defined)

1. A `Factory` has several `Brands` (between 1 and 10 per factory).
2. Fixed theme categories:
   - `BUSINESS_MONEY`
   - `PSYCHOLOGY_RELATIONSHIPS`
   - `STORIES_CURIOSITIES`
   - `CONTROVERSIES_DEBATE`
   - `COMEDY_HUMOR`
3. Category → brand relation is **1:1** (one category feeds one brand only).
4. Short slot accepts only shorts; long slot only longs (not interchangeable).
5. `Factory` has a default timezone (e.g. BR and US later).
6. Publishing retry:
   - 3 attempts;
   - 5 minute interval;
   - on failure, do not block pipeline; continue to next item.
7. Publish in small batches / serial per channel (avoid single API burst).
8. Diversity rule uses `source_asset_id` (original video ID):
   - avoid more than 2 consecutive posts with same `source_asset_id`;
   - fallback allowed when stock is not diversified.
9. After successful publish:
   - delete local media files;
   - keep log and metadata for future analytics.

---

## Functional scope

### 1) Factory context in UI

Add `Factory` selector at top of app (with current brand context).

When a factory is selected, sidebar should show:

- `Dashboard` (future; this phase is structural)
- `Brands`
- `Cut creation`
- `Video bank`
- `Schedules`
- `Posted videos`

### 2) Brands inside the factory

In brand create/edit, include:

- `name` (posting channel name);
- `theme_category` (fixed brand category);
- `logo` (file);
- `thumbnail_font_family`;
- `thumbnail_band_color`;
- `thumbnail_text_color`;
- `thumbnail_effect_color`;
- `description_suffix` (extra description text);
- `min_short_interval_minutes`;
- `min_long_interval_minutes`;
- `max_shorts_per_day`;
- `max_longs_per_day`;
- `short_window_start` / `short_window_end`;
- `long_window_start` / `long_window_end`.

### 3) Cut creation with automatic routing

Flow keeps current base (transcription → LLM/xAI → cuts), but requires:

- mandatory `theme_category` in LLM payload;
- strict validation: only one of the 5 categories;
- store `theme_category` metadata;
- route cut directly to matching brand.

### 4) Media finalization by type

- Short:
  - framing with zoom (when applicable to current flow);
  - cover without logo (band + title with brand preset only).
- Long:
  - standard finalization;
  - subtitles if selected;
  - brand logo top-left (`50x50 px`).

### 5) Per-brand video bank

Operational list per brand and type:

- `Shorts` tab
- `Long-form` tab

Each item should store at least:

- status (`AVAILABLE`, `SCHEDULED`, `POSTING`, `POSTED`, `FAILED`, `RETRY_WAIT`);
- viral score/rank;
- `source_asset_id`;
- origin analysis/job/cut;
- timestamps and publish metadata.

### 6) Automatic daily scheduling

Every day at 11:00 (factory timezone), for each brand:

1. read available stock in video bank;
2. generate short schedule within short window;
3. generate long schedule within long window;
4. respect:
   - max per day per type;
   - min interval per type;
   - window per type;
5. if item does not fit at end of window, keep for next day;
6. do not use past times if an item enters the same day after initial generation.

### 7) Posting order

Combined rules:

1. sort candidates by ascending score (lower → higher), keeping best for end of window;
2. apply diversity by `source_asset_id`:
   - avoid sequence >2 identical;
   - fallback when not enough diversity.

### 8) Publishing and retries

Schedule queue executor:

- process per brand/channel in small batch (serial per channel);
- for each item:
  - try to publish;
  - on temporary error, schedule retry in 5 min;
  - max 3 attempts;
  - when exceeded, mark `FAILED` and continue.

Log each attempt under `Schedules` for debugging.

---

## Data model (proposal)

## Factory

- `id`
- `name`
- `timezone` (IANA, e.g. `America/Sao_Paulo`, `America/New_York`)
- `is_active`
- `created_at`, `updated_at`

## Brand (extension)

- `factory_id` (FK)
- `theme_category` (unique enum)
- `logo`
- `thumbnail_font_family`
- `thumbnail_band_color`
- `thumbnail_text_color`
- `thumbnail_effect_color`
- `description_suffix`
- `min_short_interval_minutes`
- `min_long_interval_minutes`
- `max_shorts_per_day`
- `max_longs_per_day`
- `short_window_start`
- `short_window_end`
- `long_window_start`
- `long_window_end`

Constraints:

- uniqueness of `theme_category` per `factory` (1:1 rule inside factory);
- window validation (start < end);
- intervals and limits >= 0.

## VideoInventoryItem (new)

- `id`
- `factory_id`
- `brand_id`
- `video_type` (`SHORT`, `LONG`)
- `file_path` (or storage pointer)
- `title`
- `description`
- `viral_score`
- `source_asset_id` (original video ID)
- `source_metadata` (optional json)
- `origin_job_id` / `origin_cut_id` (nullable)
- `status`
- `scheduled_for` (nullable)
- `posted_at` (nullable)
- `attempt_count`
- `last_error`
- `created_at`, `updated_at`

## PostingSchedule (new)

- `id`
- `factory_id`
- `brand_id`
- `video_inventory_item_id`
- `video_type`
- `scheduled_at`
- `status` (`PLANNED`, `POSTING`, `DONE`, `FAILED`, `SKIPPED`)
- `attempt_count`
- `next_retry_at`
- `created_at`, `updated_at`

## PostingAttemptLog (new)

- `id`
- `posting_schedule_id`
- `attempt_number`
- `started_at`, `finished_at`
- `result` (`SUCCESS`, `ERROR`)
- `error_code`, `error_message`
- `provider_response` (optional json)

## PostedVideoLog (new)

- `id`
- `factory_id`
- `brand_id`
- `video_inventory_item_id`
- `external_platform` (e.g. YT/YTB)
- `external_video_id`
- `posted_at`
- `metadata_snapshot` (json)

---

## LLM/xAI contract (required change)

Add mandatory field in response:

```json
{
  "theme_category": "BUSINESS_MONEY"
}
```

Validation:

- if empty or invalid: retry classification step;
- after N failures (config), mark cut as classification error.

Note: this field can be added to all requests without breaking flows that do not consume it yet.

---

## Daily scheduling algorithm (pseudo)

```text
for each factory (active):
  now_factory_tz = now in factory.timezone
  if local_time == 11:00:
    for each brand in factory:
      generate_short_slots(brand.short_window, brand.min_short_interval, brand.max_shorts_per_day)
      generate_long_slots(brand.long_window, brand.min_long_interval, brand.max_longs_per_day)

      short_candidates = inventory AVAILABLE + type SHORT + brand
      long_candidates  = inventory AVAILABLE + type LONG + brand

      sort by viral_score asc
      apply diversity by source_asset_id (avoid >2 consecutive identical; fallback if needed)

      fill slots with candidates:
        - respect window and intervals
        - do not use past times on current day
        - if no fit, keep item AVAILABLE for next cycle

      create PostingSchedule status=PLANNED
      mark inventory SCHEDULED when linked
```

---

## Publishing algorithm (pseudo)

```text
select schedules PLANNED with scheduled_at <= now
sort by brand (batch per channel) and scheduled_at

for each schedule:
  try publish (one attempt at a time)
  if success:
    mark schedule DONE
    mark inventory POSTED
    write PostedVideoLog
    delete physical media
  if temporary error and attempt_count < 3:
    attempt_count += 1
    schedule.next_retry_at = now + 5 min
    status = PLANNED (or RETRY_WAIT)
  if final error:
    status = FAILED
    inventory = FAILED (or AVAILABLE, per future policy)
    continue next without blocking queue

always write PostingAttemptLog
```

---

## Menus and screens (MVP)

1. `Factory selector` (top bar)
2. `Brands` (CRUD + schedule rules + view)
3. `Cut creation` (current flow with category routing)
4. `Video bank` (queue per brand/type)
5. `Schedules` (day list + status + retries + errors)
6. `Posted videos` (history for future analytics)

`Dashboard` reserved for a later phase.

---

## Sprint implementation strategy

### Sprint 1 — Database and admin

- create `Factory` entity;
- link `Brand` to factory;
- add new brand fields;
- category 1:1 constraints per factory;
- basic factory/brand CRUD in admin.

**Acceptance**
- create factory with timezone;
- create 1..10 brands with unique categories;
- save window/interval/limit rules.

### Sprint 2 — Classification and routing

- adjust LLM contract for `theme_category`;
- validate required enum;
- route cuts to brand by category;
- create `VideoInventoryItem` rows.

**Acceptance**
- cut with valid category lands in correct brand bank;
- invalid category yields controlled error.

### Sprint 3 — Per-brand media finalization

- apply thumbnail/description presets;
- short vs long rules;
- `50x50` logo top-left on longs.

**Acceptance**
- visual output matches brand preset;
- short without logo; long with logo.

### Sprint 4 — Daily scheduler

- daily job 11:00 per factory timezone;
- slot generation per type;
- sort by score asc;
- diversity by `source_asset_id`;
- do not use past times.

**Acceptance**
- day schedule created correctly;
- overflow items go to next day.

### Sprint 5 — Publish executor + retry + logs

- serial batch per channel;
- retry 3x/5min;
- detailed attempt logs;
- do not block queue on failure.

**Acceptance**
- error on one item does not stop others;
- full logs on schedules screen.

### Sprint 6 — Post-publish and history

- write `PostedVideoLog`;
- remove local media after success;
- posted videos screen per brand.

**Acceptance**
- media removed only after success;
- history keeps metadata for analytics.

---

## Risks and mitigation

1. **Burst/API limit**
   - small batches and serial per channel.
2. **Inconsistent LLM classification**
   - closed enum + strict validation + classification retry.
3. **Timezone/DST conflict**
   - store IANA timezone on factory; always convert in backend.
4. **Low stock per type**
   - leave slot empty per business rule and log reason.
5. **Low origin diversity**
   - explicit fallback when no alternative for `source_asset_id`.

---

## Global MVP acceptance criteria

1. Run multiple factories with independent timezones.
2. Run multiple brands per factory with unique category.
3. Generate and route cuts with mandatory `theme_category`.
4. Maintain video bank per brand/type with trackable states.
5. Schedule daily respecting windows, limits, and intervals.
6. Publish with retry 3x/5min without blocking pipeline.
7. Log attempts and failures for debug.
8. Preserve posted history and remove local media after success.

---

## Out of scope for this phase

- advanced metrics dashboard and analytics;
- optimization by historical time performance;
- automatic topic recommendation via learning loop.
