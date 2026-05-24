# Multiple-Creator

## O que é

Gera cortes de **um único vídeo** para **N brands em paralelo**, com uma transcrição compartilhada e N chamadas independentes ao LLM (uma por brand). Cada brand recebe títulos, hooks e thumbnails distintos sem repetir a transcrição.

## Fluxo end-to-end

1. **POST `/api/multiple-creator/`** (multipart): cria `MultipleCreatorJob` em `PENDING_TRANSCRIPTION` + N `MultipleCreatorBrandExecution` em `PENDING`.
2. View dispara `multiple_creator_transcribe_task.delay(job_id)` (fila `transcription`).
3. Task baixa (se YouTube) e transcreve **uma vez** → `transcript_segments` no job → status `READY` → dispara `multiple_creator_fanout_task.delay(job_id)`.
4. Fanout cria N `AutoCutAnalysis` filhas (uma por execution) com `target_brand`, `transcript_segments` pré-populado e `file`/`source` reaproveitado do job. Linka `BrandExecution.auto_cut_analysis` em transação atômica. Move job → `RUNNING_BRANDS`. Enfileira `analyze_auto_cuts_task` por filha.
5. `analyze_auto_cuts_task` detecta a origem multi-creator (FK reverso BrandExecution → AutoCutAnalysis) e **pula** o download YouTube + transcrição (curto-circuito), cai direto na fase de análise LLM. Render normal segue ao final via `finalizar_auto_cut_task`.
6. Signal `post_save` em `AutoCutAnalysis` (apps/multiple_creator/signals.py): quando filha vai para `done`/`error`, fecha `BrandExecution` correspondente e recalcula agregado do job:
   - todos DONE → `DONE`
   - todos ERROR → `ERROR`
   - misto terminal → `PARTIAL`

## Endpoints

| Método | Rota | Função |
|---|---|---|
| `POST` | `/api/multiple-creator/` | Cria job + executions; dispara transcribe |
| `GET` | `/api/multiple-creator/` | Lista jobs (paginado) |
| `GET` | `/api/multiple-creator/<id>/` | Detalhe do job + executions |
| `POST` | `/api/multiple-creator/<id>/retry/?brand_id=X` | Retry granular: reseta a execution da brand X, descarta a `AutoCutAnalysis` antiga e dispara nova análise reaproveitando a transcrição. Retorna 409 se em andamento, 404 se brand fora do job, 400 sem transcrição ou sem `brand_id`. |

Payload do POST (multipart ou JSON):

```jsonc
{
  "file": "<arquivo>",         // OU
  "source": 123,                // OU
  "youtube_url": "https://..., // OU
  "brand_ids": [12, 47, 81],
  "name": "Live X",
  "assunto": "tema",
  "convidados": "Fulano",
  "prompt_version": "viral",
  "vertical_mode": "zoom_crop",
  "shorts_target": 12,
  "longs_target": 3,
  "thumbnail_font": "impact",
  "thumbnail_band_color": "#E12E20",
  "thumbnail_text_color": "#0A0A0A",
  "thumbnail_stroke_color": "#FFEBDC"
}
```

Validações: exatamente uma origem; `brand_ids` ≥ 1 (dedupe automático); brands existentes; autenticação obrigatória.

## Estados

`MultipleCreatorJob.status`:
- `PENDING_TRANSCRIPTION` → `TRANSCRIBING` → `READY` → `RUNNING_BRANDS` → terminal (`DONE` / `PARTIAL` / `ERROR`)

`MultipleCreatorBrandExecution.status`:
- `PENDING` → `ANALYZING` → terminal (`DONE` / `ERROR`)

Retry granular reabre o job para `RUNNING_BRANDS` e a execution para `ANALYZING`.

## Observabilidade

### Eventos (log_event)

- `multiple_creator_transcription_started` / `_finished`
- `multiple_creator_transcription_skipped` — emitido pela `analyze_auto_cuts_task` quando o curto-circuito ativa (filha de Multi-Creator)
- `multiple_creator_fanout_started` / `_finished`
- `multiple_creator_completed` — quando o job vira terminal (`success_count`, `failure_count`, `total_duration_ms`)

Todos carregam `multi_creator_job_id` para correlacionar timeline ponta a ponta.

### Métricas Prometheus (`apps/common/metrics.py`)

- `multiple_creator_jobs_total{result="DONE"|"PARTIAL"|"ERROR"}` — incrementado pelo signal no primeiro fechamento.
- `multiple_creator_brand_executions_total{result="DONE"|"ERROR"}` — incrementado a cada filha finalizada.
- `multiple_creator_duration_ms` — duração total (criação → terminal).
- `multiple_creator_transcription_savings_ms` — `transcribe_duration × (N_brands − 1)`. Vale ouro pra justificar a feature.

### Custo Grok

`grok_cost_usd_total{model=...}` continua sendo o counter de custo. Multi-Creator faz **N chamadas** (uma por brand), então o custo escala linearmente com `brand_ids`. Sem hard limit no submit — apenas telemetria; criar alerta no Grafana quando custo médio por submit ultrapassar o limiar definido pelo time.

## Idempotência e resiliência

- Cada task verifica `status` antes de agir (`transcribe` só roda em `PENDING_TRANSCRIPTION`, `fanout` só em `READY`).
- `MultipleCreatorBrandExecution` tem `UniqueConstraint(job, brand)` — retry da mesma brand não duplica.
- Criação da `AutoCutAnalysis` filha + linkagem da execution roda em `transaction.atomic`, garantindo que o curto-circuito da `analyze_auto_cuts_task` enxergue a origem multi-creator desde o início.
- Retry granular: limpa `auto_cut_analysis`, reseta `started_at`/`finished_at`/`error`, status → `PENDING`, dispara nova análise (a `AutoCutAnalysis` antiga fica órfã — não é deletada para preservar histórico).

## Troubleshooting

| Sintoma | Possível causa |
|---|---|
| Job preso em `PENDING_TRANSCRIPTION` | Worker da fila `transcription` parado. Verificar `docker compose logs celery`. |
| Job em `READY` por muito tempo | `multiple_creator_fanout_task` não enfileirou — checar `multiple_creator_fanout_started` no log. |
| Brand em `ANALYZING` por muito tempo | `analyze_auto_cuts_task` rodando ou travada — checar logs com `multi_creator_job_id`. |
| `transcription_skipped` não aparece | Filha não foi reconhecida como multi-creator (gate `_was_transcript_prepopulated_by_multi_creator` falhou); confirmar que `BrandExecution.auto_cut_analysis_id` está populado. |
| Retry retorna 409 | Execução ainda em `PENDING`/`ANALYZING`/`FINALIZING`. Aguardar terminal antes do retry. |
| Custos Grok altos | Esperado: N brands = N chamadas. Avaliar limitar `brand_ids` no client se necessário. |

## Limpeza de arquivos

Política decidida: reter `MultipleCreatorJob.file` por **24h** após o job ir para terminal, para permitir retry granular reaproveitando o arquivo. Implementado via Celery beat diário (`cleanup_terminal_job_files_task`, agendado em `config/celery.py` para 03:15 UTC, fila `processing`).

- Janela controlada por `MULTIPLE_CREATOR_FILE_RETAIN_HOURS` (default 24).
- Critério: `status IN (DONE, PARTIAL, ERROR) AND updated_at < now - retain_hours`.
- Retry granular reabre o job (status → `RUNNING_BRANDS`) e refaz o `updated_at`, então preserva o arquivo automaticamente.
- Log do resultado: evento `multiple_creator_cleanup_finished` com `removed`, `skipped_missing`, `errors`.
