# Planejamento Técnico — Banco de Vídeos (separação) + Multiple-Creator

> **Escopo deste documento:** apenas planejamento. Nenhuma alteração de código, migration, template, task Celery ou fluxo será feita aqui. Tudo é incremental, seguro e baseado no estado atual do repositório.

---

## 1. Resumo executivo

São duas melhorias independentes na plataforma Social Automation, planejadas de forma incremental:

1. **Banco de Vídeos — separar visualmente "aguardando postagem" e "postados" em blocos independentes**, com **paginações independentes** (20 por página para aguardando, 10 por página para postados). Hoje os dois grupos coexistem dentro de uma única paginação cliente-side de 25 itens, e a lista de postados acaba "empurrando" a de aguardando.
2. **Multiple-Creator — nova aba** que permite criar cortes de **um único vídeo para várias brands simultaneamente**, aproveitando **uma transcrição compartilhada** e disparando **N chamadas independentes ao LLM** (uma por brand), evitando títulos, hooks e textos idênticos entre canais.

A escolha pelas duas alterações no mesmo documento é proposital: o Banco de Vídeos é o primeiro local em que o efeito do Multiple-Creator vai aparecer (vários itens novos no inventário, de várias brands, gerados de um único upload). Manter o Banco com paginação cara e listas concorrentes prejudicaria a UX justamente quando o Multiple-Creator estiver gerando volume.

---

## 2. Levantamento do estado atual

Tudo a seguir é fato observado no repositório. Itens com incerteza estão marcados como **(ponto a confirmar)**.

### 2.1 Stack

- Backend: Django 5.2 + DRF + SimpleJWT. Apps relevantes: `apps/api`, `apps/auto_cuts`, `apps/brands`, `apps/cuts`, `apps/jobs`, `apps/mediahub`, `apps/social`, `apps/common`.
- Async: Celery 5 com filas isoladas — `CELERY_QUEUE_TRANSCRIPTION`, `CELERY_QUEUE_RENDER`, fila de publish. Beat para agendamento.
- Frontend: React 18 + Vite, em `frontend/src/`. Rotas em `frontend/src/App.jsx`. Menu lateral em `frontend/src/components/Layout.jsx`.
- Observabilidade: logs JSON estruturados via `apps/jobs/logging_utils.py` (`log_event`, `correlation_id` via `ContextVar`, `Timer`), métricas Prometheus em `apps/common/metrics.py` (counters/histograms para `task`, `transcription`, `render`, `publish`, `grok`).
- Paginação DRF padrão: `apps/api/pagination.py` → `StandardResultsSetPagination` (`page_size=25`, `page_size_query_param="page_size"`, `max_page_size=100`).
- Componente de paginação no frontend: `frontend/src/components/PaginationControls.jsx` (`DEFAULT_PAGE_SIZE = 25`).
- Helper de resposta paginada: `normalizeListResponse` em `frontend/src/api.js` (linha ~79) — espera `{count, results}` e devolve `{items, count}`.

### 2.2 Banco de Vídeos da Factory

- **Página:** `frontend/src/pages/BancoVideos.jsx`.
  - Faz uma única chamada `getVideoInventory({factoryId, brandId, videoType, page, pageSize})`.
  - Recebe a página inteira e filtra cliente-side: `awaitingItems = items.filter(s !== 'POSTED')` e `postedItems = items.filter(s === 'POSTED')`.
  - Renderiza dois blocos visuais (`Aguardando Postagem`, `Vídeos Postados`) dentro do mesmo `<section>`, com **uma única `PaginationControls`** abaixo dos dois.
  - Resumo (`summary`) usa o total da API, mas as contagens "aguardando/postados" são da página corrente — daí labels como "Aguardando (página)".
- **API frontend:** `getVideoInventory` em `frontend/src/api.js` (linha 328). Endpoint: `GET /api/video-inventory/?factory=&brand=&status=&video_type=&page=&page_size=`.
- **Backend:** `VideoInventoryItemViewSet(viewsets.ReadOnlyModelViewSet)` em `apps/api/views.py` (linha 1151). Usa `StandardResultsSetPagination`. `get_queryset()` aplica filtros `factory`, `brand`, `status`, `video_type` e ordena por `-created_at`.
  - Já existe filtro por `status` no querystring, **mas hoje o frontend não envia esse parâmetro** — vem tudo misturado.
  - Possui `@action`s detalhadas: `remove-awaiting`, `retry-posting`, `download-media`, `mark-posted`.
- **Modelo:** `VideoInventoryItem` em `apps/jobs/models.py` (linha 460).
  - `STATUS`: `AVAILABLE`, `SCHEDULED`, `POSTING`, `POSTED`, `FAILED`.
  - `VIDEO_TYPE`: `SHORT`, `LONG`.
  - Campos relevantes: `factory`, `brand`, `auto_cut_corte` (OneToOne para `AutoCutCorte`), `status`, `scheduled_for`, `posted_at`, `attempt_count`, `last_error`, `created_at`.
  - Ordering: `["status", "-virality_score", "id"]`.
- **Serializer:** `VideoInventoryItemSerializer` em `apps/api/serializers.py` (linha 765). Já entrega `source_display_name`, `status_message`, `scheduled_post_id`.
- **Testes existentes:** `apps/api/tests/test_api.py::VideoInventoryMarkPostedTests` cobre `mark-posted`. **Não há teste cobrindo a paginação do listing por status separados** (ponto a confirmar — pesquisa por `VideoInventory` no `tests/` retornou só os de mark-posted).

### 2.3 Criação de Cortes (aba atual)

- **Página:** `frontend/src/pages/CortesAutomaticos.jsx`. No menu lateral aparece como **"Cortes Automáticos"** (modo brand) ou **"Criação de Cortes"** (modo factory) — ver `frontend/src/components/Layout.jsx`.
  - Permite: upload de arquivo, escolha de `source` existente, ou `youtube_url`. Escolha de `brand`, opcional `target_brand`, `distribution_mode` (`theme` | `distribute`), prompt version, thumbnail, etc.
  - Submete via `createAutoCutAnalysis(...)` em `frontend/src/api.js` (linha 920).
- **Endpoint:** `POST /api/auto-cuts/` → `AutoCutAnalysisViewSet.create` em `apps/api/views.py` (linha 1598). Cria `AutoCutAnalysis` e enfileira `analyze_auto_cuts_task.delay(analysis.id)`.
- **Modelo principal:** `AutoCutAnalysis` em `apps/auto_cuts/models.py` (linha 9).
  - Tem **uma** `brand` (FK), opcional `target_brand` (envia todos os cortes para essa brand, ignorando `theme_category` da IA), e `distribution_mode` (`theme` usa categoria da IA; `distribute` distribui pela brand com menos vídeos no banco).
  - Campos de pipeline: `status` (`pending`, `transcribing`, `analyzing`, `finalizing`, `done`, `error`), `progress`, `progress_message`, `transcript`, `transcript_segments`, `error`.
  - Pode ter um `SourceVideo` (`source` FK), um `file` direto ou `youtube_url`.
  - Modelos derivados: `AutoCutSuggestion` (sugestões do LLM), `AutoCutCorte` (corte extraído), `AutoCutReadyChunk` (lote de cortes prontos).
- **Task de pipeline:** `analyze_auto_cuts_task` em `apps/auto_cuts/tasks.py` (linha 867). Pipeline atual para 1 análise:
  1. Sanity checks + pausa cooperativa (`factory.processing_paused`).
  2. Se `is_ready_cuts`: rota separada (`_process_ready_cuts_batch_flow`).
  3. Se `youtube_url`: baixa via `services/youtube_download.py`.
  4. Transcrição (Whisper) — para vídeos longos divide em chunks (`extract_chunks_to_folder` → `transcribe_single_chunk`); para curtos usa `generate_subtitles` em uma passada.
  5. Persiste `transcript_segments` + `transcript` no `AutoCutAnalysis`.
  6. **Uma única chamada LLM**: `analyze_chunks_in_one_request(...)` de `services/grok.py`, com `prompt_version`, `allowed_theme_categories`, `brand_only`. Retorna `candidate_shorts`, `ranked_shorts`, `final_long_cuts`.
  7. Filtra/ordena, cria `AutoCutSuggestion`s e extrai cortes (`extract_corte`, `generate_auto_thumbnail`), criando `AutoCutCorte`s.
  8. Para cada corte, `_resolve_target_brand_for_suggestion` decide a brand de destino (priorizando `target_brand`, depois `distribute`, depois `theme_category`).
  9. Enfileira `finalizar_auto_cut_task` (fila `CELERY_QUEUE_RENDER`) que faz reframe vertical, queima de legenda, overlay e marca cortes como finalizados.
  10. `_sync_inventory_item_from_corte` cria/atualiza o `VideoInventoryItem` correspondente — é assim que o Banco de Vídeos se popula.

### 2.4 Pontos de extensão úteis para Multiple-Creator

- `AutoCutAnalysis.source` (FK opcional para `SourceVideo`) — sinaliza que o mesmo `SourceVideo` já pode ser referenciado por múltiplas análises.
- `transcript_segments` é JSONField, fácil de copiar entre análises filhas.
- `target_brand` força "todos os cortes vão para essa brand" — combinado com **N análises filhas**, cada uma com um `target_brand` diferente, replica o comportamento desejado **sem refatorar** `_resolve_target_brand_for_suggestion`.
- Observabilidade já tem `correlation_id` por análise/job; basta propagar.

### 2.5 Observabilidade existente (a seguir como padrão)

- `apps/jobs/logging_utils.py`: `log_event(logger, event=..., correlation_id=..., status=..., duration_ms=..., **extra)`, `ensure_job_correlation_id`, `Timer`.
- `apps/common/metrics.py`: contadores e histogramas com label `workload_type` (`cpu`/`gpu`).
- Eventos já usados no pipeline: `transcription_started`, `transcription_finished`, `render_started`, `render_finished`, `publish_started`, `publish_finished`, etc.

---

## 3. Plano da Alteração 1 — Banco de vídeos

Objetivo: dois blocos verdadeiramente separados, com paginações independentes (20 aguardando, 10 postados), na mesma página.

### Etapa 1 — Localizar origem dos dados (concluído neste planejamento)

- Backend: `VideoInventoryItemViewSet` em `apps/api/views.py:1151`.
- Frontend: `BancoVideos.jsx` + `getVideoInventory` em `api.js:328`.
- O endpoint já aceita `status` no querystring, mas **só aceita 1 valor**. Para "aguardando" precisamos `status != POSTED` (vários valores).

### Etapa 2 — Definir granularidade no backend (escolha entre 3 opções)

> Trade-off importante. As três opções abaixo preservam o endpoint atual e são incrementais.

- **Opção A (recomendada) — agrupamento via querystring "bucket":** adicionar suporte a `?bucket=awaiting` e `?bucket=posted` em `get_queryset()`.
  - `awaiting` → `status__in=["AVAILABLE", "SCHEDULED", "POSTING", "FAILED"]`.
  - `posted` → `status="POSTED"`.
  - Mantém o endpoint REST único e a paginação DRF padrão, sem novos modelos/serializers.
  - Permite passar `page_size=20` para um bucket e `page_size=10` para outro de forma totalmente independente.
- **Opção B — dois `@action`s detalhados:** `GET /api/video-inventory/awaiting/` e `GET /api/video-inventory/posted/`.
  - Mais explícito e fácil de versionar, mas exige duplicar contrato (mais código e mais um lugar para evoluir).
- **Opção C — manter um único endpoint e separar somente no frontend:** já é o que existe hoje, com filtro cliente-side; **não atende ao requisito** porque as paginações não conseguem ser realmente independentes (uma página da API já pode vir 100% postados ou 100% aguardando).

**Decisão recomendada: A**, pelo menor blast radius. **Ponto a confirmar:** se o time prefere a explicitação da Opção B (relevante caso queiramos ordenações diferentes por bucket — por exemplo "aguardando" por `scheduled_for` e "postados" por `posted_at`).

### Etapa 3 — Frontend: duas chamadas independentes e dois estados de paginação

Mudanças confinadas a `frontend/src/pages/BancoVideos.jsx` e `frontend/src/api.js`:

- Em `api.js`: aceitar `bucket` em `getVideoInventory({...})` e propagar como `params.append('bucket', bucket)`.
- Em `BancoVideos.jsx`:
  - Estados separados: `awaitingItems`, `awaitingPage`, `awaitingTotal`; `postedItems`, `postedPage`, `postedTotal`.
  - Dois `useEffect` independentes (um por bucket) com dependências separadas.
  - Dois `PaginationControls` (um por bloco).
  - Passar `pageSize={20}` para aguardando e `pageSize={10}` para postados (ambos vindos como prop ao componente).
- Nomes dos parâmetros de query string (resposta direta ao requisito): **`pending_page` / `posted_page`** **só** se mantivermos a URL com paginações refletidas na barra de endereço; caso contrário (o frontend atual mantém estado em React, sem sincronizar com query da rota), basta `page` por chamada — não há conflito porque são requisições HTTP separadas.
  - **Recomendado:** manter o estado em React (sem refletir paginação na URL da rota); se no futuro for desejável sincronizar, adotar `pending_page` / `posted_page` na URL da rota (não no endpoint backend).

### Etapa 4 — Ajustar template/layout

- Garantir que os títulos "Aguardando Postagem" e "Vídeos Postados" e suas tabelas continuem visualmente claros mesmo quando um bloco estiver vazio (atualmente já há `empty-msg`).
- Resumo (`banco-summary`): reescrever para refletir os dois `total`s independentes:
  - `Aguardando (total): N` e `Postados (total): M`, sem mais "por página".
  - Manter "Disponíveis/Postando/Erros" se forem úteis — agora baseados na página corrente de aguardando.
- `PaginationControls` aceitar `pageSize` como prop (já aceita); confirmar que o cálculo de `totalPages` usa o `pageSize` específico (já usa).
- **Ponto a confirmar:** o `summary.SCHEDULED + summary.POSTING` mostrado hoje conta da **página** atual, não do total — após a separação, esse comportamento permanece (ou removemos esse campo).

### Etapa 5 — Testes

- Backend (`apps/api/tests/test_api.py`):
  - `GET /api/video-inventory/?bucket=awaiting` retorna apenas não-POSTED.
  - `GET /api/video-inventory/?bucket=posted` retorna apenas POSTED.
  - `page_size=20` em `awaiting` e `page_size=10` em `posted` respeita o `max_page_size=100`.
  - Filtros existentes (`factory`, `brand`, `video_type`) continuam funcionando combinados com `bucket`.
  - Sem `bucket`: comportamento legado preservado (não quebra clientes antigos).
- Frontend (sem framework de teste de UI definido no repo — **ponto a confirmar** se há setup de Jest/Vitest para `frontend/`; se sim, adicionar; se não, validação manual). Caso exista, casos sugeridos:
  - Renderiza dois blocos com paginadores independentes.
  - Mudar página em "Aguardando" não recarrega "Postados".
  - Filtro `videoType` recalcula `page=1` em ambos.

### Etapa 6 — Validação manual

- Carregar página com dois blocos cheios.
- Navegar entre páginas em ambos.
- Trocar factory / brand: ambos blocos voltam a `page=1`.
- Trocar `videoType`: ambos blocos voltam a `page=1`.
- Forçar bloco vazio (filtros que não retornem nada) — confirmar empty-msg.
- Executar ações existentes (`remove-awaiting`, `retry-posting`, `mark-posted`, `download-media`) e verificar que afetam apenas o bloco correspondente.

---

## 4. Plano da Alteração 2 — Multiple-Creator

> Filosofia: **não tocar** no fluxo atual de `CortesAutomaticos`. Construir o Multiple-Creator **ao lado**, reusando `AutoCutAnalysis`, `analyze_auto_cuts_task`, `finalizar_auto_cut_task` e o roteamento por `target_brand`. Adicionar uma camada fina de **orquestração** por cima.

### Etapa 1 — Mapear fluxo atual (já documentado na seção 2.3/2.4)

Resumo crítico para o desenho: para enviar o **mesmo vídeo** para **N brands** com transcrição única, precisamos disparar **N análises filhas**, cada uma:
- com `target_brand_id` apontando para uma brand selecionada,
- com `transcript_segments` e `transcript` pré-populados a partir de uma transcrição executada apenas uma vez,
- ignorando as etapas 3 e 4 do pipeline atual (download YouTube + transcrição), pulando direto para a chamada LLM.

### Etapa 2 — Definir modelo de request/formulário

`POST /api/multiple-creator/` (novo endpoint, novo `@api_view`/ViewSet — proposta na seção 5). Payload:

```jsonc
{
  // Origem do vídeo (exatamente uma das três):
  "file": "<arquivo>",          // multipart
  "source_id": 123,              // SourceVideo existente
  "youtube_url": "https://...",

  // Lista de brands selecionadas (de qualquer factory)
  "brand_ids": [12, 47, 81],

  // Metadados compartilhados (iguais aos do AutoCutAnalysis atual)
  "name": "Live X",
  "assunto": "...",
  "convidados": "...",
  "prompt_version": "viral",
  "vertical_mode": "zoom_crop",
  "shorts_target": 12,
  "longs_target": 3,
  "thumbnail_font": "impact",
  "thumbnail_band_color": "#E12E20",
  "thumbnail_text_color": "#0A0A0A",
  "thumbnail_stroke_color": "#FFEBDC",
  // long_overlay_* são por brand: ver Etapa 4
}
```

Validações backend:
- `brand_ids` ≥ 1; cada id existe; usuário tem permissão (mesma checagem que `AutoCutAnalysisViewSet`).
- Exatamente uma origem (file/source/youtube_url) — mesma regra atual (`sources_count == 1`).
- `prompt_version` ∈ choices; `vertical_mode` ∈ choices.
- Sem duplicidade em `brand_ids` (set).

### Etapa 3 — Criar UI da nova aba

- Nova rota: `/multiple-creator` (modo "factory" e "brand" — discutir em Etapa 6 da arquitetura).
- Adicionar `NavLink` em `frontend/src/components/Layout.jsx` (`brandMenuLinks` e/ou `factoryMenuLinks`).
- Novo arquivo: `frontend/src/pages/MultipleCreator.jsx`. Reaproveitar formulário de `CortesAutomaticos.jsx`, removendo o seletor de `target_brand` único e adicionando seletor múltiplo:
  - **Componente sugerido:** checkboxes agrupados por factory (mesma fonte usada em `useBrand()`/`getBrands()` no frontend), com chip de "X brands selecionadas" no topo e busca por nome. Modal opcional se a lista crescer muito.
- Validar no submit: lista de brand_ids não vazia; arquivo OU URL selecionado.

### Etapa 4 — Implementar seleção múltipla de brands

- Buscar brands com `getBrands()` (já existente em `frontend/src/api.js`) — endpoint `/api/brands/` retorna todas (paginação aplicada — **ponto a confirmar:** confirmar se `getBrandsAllPages()` ou equivalente existe; ver `getSourcesAllPages` para padrão).
- Estado: `selectedBrandIds: Set<number>`.
- Persistência: opcionalmente memorizar a última seleção no `localStorage` (UX). Não obrigatório na primeira versão.
- Edge case: brand sem factory (orfã) — exibir como "Sem factory" para o usuário identificar.

### Etapa 5 — Reaproveitar transcrição única

Dois caminhos possíveis:

- **Caminho A (recomendado): transcrição é executada uma vez no orquestrador, antes de spawnar as análises filhas.**
  1. O `MultipleCreatorJob` (ver Seção 5) cria um `SourceVideo` (ou anexa o existente) e dispara uma task `multiple_creator_transcribe_task`.
  2. A task baixa (se YouTube) e transcreve uma única vez, gravando `transcript_segments` e `transcript` no `MultipleCreatorJob` (não no `AutoCutAnalysis`).
  3. Após sucesso, spawna N `AutoCutAnalysis` filhos (1 por brand selecionada), pré-populando `transcript_segments`, `transcript`, `file` (referência ao mesmo arquivo), `youtube_url` vazio, `source` (FK ao mesmo `SourceVideo` quando aplicável), `target_brand_id = brand_i`, `distribution_mode='theme'` (irrelevante quando `target_brand_id` está setado).
  4. Cada filho entra direto na fase de análise LLM. Para evitar re-transcrever, adicionamos um curto-circuito em `analyze_auto_cuts_task`: **se `transcript_segments` já existe e `status` é `pending` e a flag de origem multi-creator estiver presente, pular para a fase "analyzing"**. (Ver Seção 6 para por que esse curto-circuito é seguro.)

- **Caminho B: cada análise filha "puxa" a transcrição do pai sob demanda.**
  - Mais simples no orquestrador, mas concorrência piora (race no `transcript_segments`). Rejeitado.

**Decisão recomendada: A.**

### Etapa 6 — Criar orquestração por brand

`MultipleCreatorJob` (Seção 5) tem campos:
- `status`: `PENDING_TRANSCRIPTION`, `TRANSCRIBING`, `READY`, `RUNNING_BRANDS`, `DONE`, `PARTIAL`, `ERROR`.
- `brand_executions`: relação 1:N para `MultipleCreatorBrandExecution` (uma por brand selecionada), com seu próprio `status` e `auto_cut_analysis` (FK).

Fluxo:
1. `POST /api/multiple-creator/` cria `MultipleCreatorJob` (status `PENDING_TRANSCRIPTION`) e N `MultipleCreatorBrandExecution` (status `PENDING`).
2. Dispara `multiple_creator_transcribe_task`.
3. Ao concluir, marca o job como `READY` e dispara `multiple_creator_fanout_task`.
4. O fanout cria N `AutoCutAnalysis` (uma por execução de brand), pré-populando transcript, e enfileira `analyze_auto_cuts_task` por cada análise filha (com idempotência via correlation_id — ver Seção 6).
5. Cada filha segue o pipeline normal (sem re-transcrever): análise LLM individual → cortes → finalização → inventário.

### Etapa 7 — Chamada individual ao LLM por brand

- Já temos `analyze_chunks_in_one_request(...)` em `apps/auto_cuts/services/grok.py`. **Sem alteração** nessa função.
- Como cada análise filha tem seus próprios `assunto`, `prompt_version`, `allowed_theme_categories` (que vêm da brand/factory da filha), o LLM retorna naturalmente cortes/títulos/hooks diferentes — o objetivo do requisito.
- Como `target_brand_id` está setado em cada filha, `_resolve_target_brand_for_suggestion` envia 100% dos cortes da filha para a brand correspondente. `theme_category` é ignorada (já é o comportamento atual quando `target_brand_id` existe).

### Etapa 8 — Envio para renderização

- Sem alteração no `finalizar_auto_cut_task`. Ele será enfileirado naturalmente ao final de cada `analyze_auto_cuts_task` filha, via `_queue_analysis_finalization` existente.
- `_sync_inventory_item_from_corte` (já existente) popula `VideoInventoryItem` para cada brand alvo.

### Etapa 9 — Tratamento de falhas parciais

- Cada `MultipleCreatorBrandExecution` tem `status` independente. Falha em uma brand:
  - Marca a execução como `ERROR` com mensagem.
  - Não interrompe as outras.
- Job pai termina como `DONE` (todas OK), `PARTIAL` (≥1 OK e ≥1 ERROR) ou `ERROR` (todas falharam).
- Retry: ação manual via `POST /api/multiple-creator/<id>/retry/?brand_id=X` que recria/reanaliza apenas a execução da brand X reaproveitando a transcrição já existente.
- Falha de transcrição (pai): job inteiro vai a `ERROR`. Sem fanout — nenhuma filha foi criada. Retry inteiro disponível.
- Ver Seção 6 para idempotência detalhada.

### Etapa 10 — Testes unitários/integrados

- Validar request inválido (sem brands, brands inexistentes, sem origem, mais de uma origem).
- Validar criação do `MultipleCreatorJob` + N `BrandExecution`s.
- **Mockar Whisper** e validar que a transcrição roda **uma vez** independentemente de N (assert: contador da task ou patch em `generate_subtitles`).
- **Mockar Grok** e validar que `analyze_chunks_in_one_request` é chamada **uma vez por brand** (assert N chamadas).
- Validar que cada `AutoCutAnalysis` filha sai com `transcript_segments` populado e `target_brand_id` setado.
- Falha em uma brand → status final `PARTIAL`; outras concluem.
- Retry de uma brand específica não duplica `AutoCutAnalysis`/cortes (idempotência por correlation_id).
- Permissão: usuário sem acesso a uma brand recebe 403 (mesma checagem do endpoint atual).

### Etapa 11 — Observabilidade/logs/métricas

Eventos novos (todos via `log_event`, mesmo padrão JSON):
- `multiple_creator_started` (status: started, brand_count, source_kind, multi_creator_job_id).
- `multiple_creator_transcription_started` / `_finished` (com `duration_ms`).
- `multiple_creator_fanout_started` / `_finished` (n_brands_spawned).
- `multiple_creator_brand_started` / `_finished` / `_failed` (com `brand_id`, `auto_cut_analysis_id`).
- `multiple_creator_completed` (status final: `done` | `partial` | `error`, `success_count`, `failure_count`, `total_duration_ms`, `transcription_savings_ms` (= duração de transcrição × (N-1), tempo economizado vs. abordagem antiga).

Métricas Prometheus novas em `apps/common/metrics.py`:
- `multiple_creator_jobs_total{result="done"|"partial"|"error"}`.
- `multiple_creator_brand_executions_total{result="ok"|"error"}`.
- `multiple_creator_transcription_savings_ms` (Histogram).

Logs reusam o `correlation_id` do `MultipleCreatorJob` em todas as filhas (propagado via `ensure_job_correlation_id`-like helper).

---

## 5. Proposta de arquitetura para o Multiple-Creator

### 5.1 Opções avaliadas

- **A (recomendada). Dois modelos novos, fluxo orquestrado:**
  - `MultipleCreatorJob` (1 por submit do usuário).
  - `MultipleCreatorBrandExecution` (N por job — 1 por brand selecionada).
  - Cada `BrandExecution` referencia um `AutoCutAnalysis` filho (FK opcional, criado no fanout).
  - **Prós:** estado claro por brand, retry granular, logs/metric agregáveis, mínima intrusão no fluxo existente, idempotência simples.
  - **Contras:** 2 modelos + 1 migration; aumento moderado de complexidade no schema.

- **B. Sem modelo novo, apenas N `AutoCutAnalysis` criadas em série:**
  - O endpoint cria N análises com `target_brand_id` e `transcript_segments` pré-populado, retorna a lista.
  - **Prós:** zero migration. Implementação rápida.
  - **Contras:** sem agregação de status; o usuário precisa "adivinhar" que N análises na lista "Cortes Automáticos" pertencem ao mesmo submit; retry e logs ficam fragmentados; observabilidade pior; difícil medir "tempo economizado".

- **C. Reaproveitar `AutoCutAnalysis` adicionando `M2M brands`:**
  - **Prós:** zero modelos novos.
  - **Contras:** **alta intrusão**. Muda contrato do modelo central usado em todo lugar (admin, serializers, todas as tasks, `_resolve_target_brand_for_suggestion`, sync de inventário). Quebra hipóteses do código existente. Rejeitado.

**Decisão recomendada: A.** O custo de 2 modelos é pequeno e o ganho em observabilidade, retry e auditoria é alto.

### 5.2 Esboço dos modelos (sem migration agora — só especificação)

```python
class MultipleCreatorJob(models.Model):
    STATUS = [
        ("PENDING_TRANSCRIPTION", "Pendente — transcrição"),
        ("TRANSCRIBING", "Transcrevendo"),
        ("READY", "Pronto para fanout"),
        ("RUNNING_BRANDS", "Processando brands"),
        ("DONE", "Concluído"),
        ("PARTIAL", "Concluído com falhas parciais"),
        ("ERROR", "Erro"),
    ]
    user = FK(User, null=True)
    name = CharField(...)
    source_kind = CharField(["FILE", "SOURCE", "YOUTUBE"])
    source = FK(SourceVideo, null=True)
    file = FileField(null=True)
    youtube_url = URLField(blank=True)
    transcript = TextField(blank=True)
    transcript_segments = JSONField(null=True)
    prompt_version, vertical_mode, shorts_target, longs_target, thumbnail_* ...
    status = CharField(choices=STATUS, default="PENDING_TRANSCRIPTION")
    progress, progress_message, error = ...
    correlation_id = CharField(max_length=64, blank=True, db_index=True)
    created_at, updated_at

class MultipleCreatorBrandExecution(models.Model):
    STATUS = [("PENDING", ...), ("ANALYZING", ...), ("FINALIZING", ...), ("DONE", ...), ("ERROR", ...)]
    job = FK(MultipleCreatorJob, related_name="brand_executions", on_delete=CASCADE)
    brand = FK(Brand, on_delete=PROTECT)
    auto_cut_analysis = FK(AutoCutAnalysis, null=True, on_delete=SET_NULL)
    status = CharField(choices=STATUS, default="PENDING")
    error = TextField(blank=True)
    started_at, finished_at
    class Meta:
        constraints = [UniqueConstraint(fields=["job", "brand"], name="uniq_brand_per_job")]
```

### 5.3 Tasks Celery propostas

- `multiple_creator_transcribe_task(job_id)` — fila `CELERY_QUEUE_TRANSCRIPTION`. Idempotente (`if job.status != PENDING_TRANSCRIPTION: return`).
- `multiple_creator_fanout_task(job_id)` — fila genérica (orquestração leve). Cria N `AutoCutAnalysis` filhas e enfileira `analyze_auto_cuts_task` por filha.
- `analyze_auto_cuts_task` (existente, sem mudança de assinatura) — só precisa de um curto-circuito: **se `transcript_segments` já existe ao entrar, pular o estágio de transcrição** (a fase de download YouTube também é pulada, porque o arquivo já está no disco).

### 5.4 Endpoint sugerido

- `POST /api/multiple-creator/` → cria job, retorna `{id, brand_executions: [...]}`.
- `GET /api/multiple-creator/<id>/` → status agregado e de cada execução.
- `POST /api/multiple-creator/<id>/retry/?brand_id=X` → retry granular.
- `DELETE /api/multiple-creator/<id>/` → cancela / remove (com cuidado se análises filhas já produziram inventário).

### 5.5 Onde cada mudança provavelmente fica

- Backend: novo app `apps/multiple_creator/` (modelos + tasks + admin) **ou** subseção em `apps/auto_cuts/` (mesma domain). **Recomendação: novo app**, para isolamento e clareza de boundary. **Ponto a confirmar:** alinhar com o time se preferem manter dentro de `auto_cuts/` para evitar mais um app.
- Endpoint: novo `MultipleCreatorViewSet` em `apps/api/views.py`, registrado em `apps/api/urls.py` (`router.register("multiple-creator", ...)`).
- Serializers: `MultipleCreatorJobSerializer`, `MultipleCreatorBrandExecutionSerializer` em `apps/api/serializers.py`.
- Curto-circuito de transcrição: `apps/auto_cuts/tasks.py::analyze_auto_cuts_task` (≤ 20 linhas no início da função, dentro do early-return atual sobre `status in ("done", "finalizing")`).
- Frontend: `frontend/src/pages/MultipleCreator.jsx`, `frontend/src/pages/MultipleCreator.css`, entrada de menu em `Layout.jsx`, helpers em `api.js`.

---

## 6. Idempotência e resiliência

Regras gerais:

- **Correlation ID**: `MultipleCreatorJob.correlation_id` é gerado no `POST` e propagado para todas as filhas (`AutoCutAnalysis`) via campo dedicado **ou** via `ContextVar` na task (padrão atual). Permite traçar todos os logs do submit.
- **Idempotência por estado**: cada task checa `status` antes de agir:
  - `multiple_creator_transcribe_task`: se `status != PENDING_TRANSCRIPTION`, retornar (evita re-rodar em retry de Celery).
  - `multiple_creator_fanout_task`: se `status != READY`, retornar.
  - `analyze_auto_cuts_task` filha: já tem early-return (`if status in ("done", "finalizing"): return`), preservar.
- **Uniqueness**: `UniqueConstraint(["job", "brand"])` em `MultipleCreatorBrandExecution` impede que o fanout crie duas execuções para a mesma brand no mesmo job (retry safe).
- **`auto_cut_analysis` ligada à execução**: o fanout faz **`get_or_create`** para a `AutoCutAnalysis` filha, usando `(multiple_creator_brand_execution_id,)` como chave única lógica (campo opcional no `AutoCutAnalysis`, **ou** consultando via FK reversa em `MultipleCreatorBrandExecution.auto_cut_analysis`). **Ponto a confirmar:** preferimos adicionar um campo `auto_cut_analysis` em `BrandExecution` (mais simples e auditável) — escolhido no esboço da Seção 5.

Cenários cobertos:

- **Retry de Celery (mesma task duas vezes)**: status check + uniqueness constraint impedem duplicação.
- **Queda no meio do processamento**: ao reiniciar, o job permanece no status em que parou; um endpoint manual `retry` reprocessa a partir do estágio correto (transcrição ainda não terminou → re-disparar `transcribe`; transcrição OK e fanout falhou → re-disparar `fanout`; uma brand falhou → re-disparar `analyze_auto_cuts_task` daquela filha).
- **Transcrição concluída mas algumas brands não processadas**: estado do job vira `RUNNING_BRANDS` ou `PARTIAL`; o usuário vê quais brands falharam; retry pontual é trivial.
- **LLM falhou para uma brand**: pipeline atual já tem `MAX_RETRIES=3` no Grok dentro de `analyze_auto_cuts_task`; após esgotar, a filha vai para `error`, sua `BrandExecution` recebe `ERROR`, e o pai segue.
- **Renderização falhou para uma brand**: `finalizar_auto_cut_task` é responsável; tem seus próprios contadores. A `BrandExecution` reflete o resultado ao receber callback (ou ao consultar o estado da filha). **Ponto a confirmar:** se precisaremos de um signal/Celery chord para fechar a `BrandExecution`, ou se um post-save signal em `AutoCutAnalysis` (`status='done'`/`error`) já basta — recomendo signal por simplicidade.

---

## 7. Observabilidade

Tudo seguindo o padrão de `apps/jobs/logging_utils.py` e `apps/common/metrics.py`.

### 7.1 Logs estruturados

| Evento                                            | Quando                                                    | Campos extras                                                                 |
|---------------------------------------------------|-----------------------------------------------------------|-------------------------------------------------------------------------------|
| `multiple_creator_started`                        | logo após `POST /api/multiple-creator/`                   | `multi_creator_job_id`, `brand_count`, `source_kind`, `user_id`               |
| `multiple_creator_transcription_started`          | início de `multiple_creator_transcribe_task`             | `multi_creator_job_id`, `workload_type`                                       |
| `multiple_creator_transcription_finished`         | fim                                                      | `duration_ms`, `segments_count`, `status` (success/error), `error?`           |
| `multiple_creator_fanout_started`                 | início de `multiple_creator_fanout_task`                 | `multi_creator_job_id`, `brand_count`                                         |
| `multiple_creator_fanout_finished`                | fim                                                      | `spawned_count`, `status`, `error?`                                           |
| `multiple_creator_brand_llm_started`              | dentro de `analyze_auto_cuts_task` filha (quando origem multi-creator) | `brand_id`, `auto_cut_analysis_id`, `multi_creator_job_id`, `prompt_version` |
| `multiple_creator_brand_llm_finished`             | fim da análise LLM                                       | `duration_ms`, `candidates`, `ranked_shorts`, `final_long_cuts`, `status`     |
| `multiple_creator_brand_render_started/finished`  | piggyback de `render_started`/`render_finished` com extra `multi_creator_job_id` |                                                                               |
| `multiple_creator_completed`                      | quando todas as filhas terminam                          | `status` (done/partial/error), `success_count`, `failure_count`, `total_duration_ms`, `transcription_savings_ms` |

### 7.2 Métricas Prometheus (novas, em `apps/common/metrics.py`)

- `multiple_creator_jobs_total{result}` (Counter).
- `multiple_creator_brand_executions_total{result}` (Counter).
- `multiple_creator_duration_ms` (Histogram, buckets já existentes).
- `multiple_creator_transcription_savings_ms` (Histogram).

### 7.3 Banco de Vídeos — observabilidade

A separação dos blocos não muda métricas; só vale registrar:
- Verificar via log/Grafana que a separação não dispara mais consultas pesadas (duas queries DRF em vez de uma).
- Confirmar que `select_related`/`prefetch_related` continuam aplicados nas duas chamadas (eles vivem em `get_queryset()`, comum a todas as listings).

---

## 8. Testes necessários

### 8.1 Banco de vídeos

- Paginação independente de aguardando: 25 itens, `page_size=20` → página 1 com 20 itens, página 2 com 5.
- Paginação independente de postados: 25 itens, `page_size=10` → páginas 1, 2 com 10 cada, página 3 com 5.
- Page=1 em "aguardando" não afeta page em "postados" (chamadas independentes — confirmar via fixtures).
- Filtro `factory`, `brand`, `video_type` aplicado a ambos buckets retorna apenas o subset esperado.
- Sem `bucket` ou com `bucket` desconhecido → comportamento legado (preserva clientes antigos).
- `max_page_size=100` continua respeitado (defesa).
- Permissões: usuário não autenticado → 401; autenticado vê o que já vê hoje.
- `mark-posted`, `remove-awaiting`, `retry-posting` continuam funcionando após a separação.

### 8.2 Multiple-Creator

- **Criação**:
  - Criação com múltiplas brands (≥ 2) válidas → 201 + job + N executions.
  - Sem brands selecionadas → 400.
  - Brand inexistente / sem permissão → 400/403.
  - Sem origem (file/source/url) → 400.
  - Mais de uma origem → 400.
- **Pipeline**:
  - Whisper mockado: `transcribe_*` chamado **exatamente 1 vez** por submit (independente de N brands).
  - Grok mockado: `analyze_chunks_in_one_request` chamado **N vezes** (uma por brand), com `target_brand_id` diferente.
  - Cada `AutoCutAnalysis` filha sai com `transcript_segments` populado antes da fase `analyzing`.
  - Renderização disparada para cada brand: `finalizar_auto_cut_task` enfileirada N vezes.
- **Falha parcial**:
  - 1 das N brands gera erro de LLM → job final em `PARTIAL`; as outras concluem normalmente.
  - Erro de transcrição → job em `ERROR`, nenhuma filha criada.
- **Retry**:
  - Retry do mesmo job pai → idempotente (status não regride).
  - Retry de uma brand específica não cria `AutoCutAnalysis` duplicada (uniqueness constraint).
- **Inventário**:
  - Após sucesso de uma brand, `VideoInventoryItem` para aquela brand existe e tem `auto_cut_corte` apontando para corte da filha correta.

### 8.3 Frontend (sob ressalva da existência de framework)

- **Ponto a confirmar:** se o projeto tem Jest/Vitest/Playwright configurado para `frontend/`. Não encontrei configuração óbvia — se ausente, a validação será manual e a sugestão é introduzir testes de UI apenas em uma fase posterior, fora desse escopo.

---

## 9. Riscos técnicos

| Risco                                                        | Mitigação                                                                                                  |
|--------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| **Duplicidade de jobs** em retry de Celery                  | `status` check + `UniqueConstraint` em `BrandExecution`; `get_or_create` no fanout.                       |
| **Sobrecarga no LLM** (N chamadas paralelas)                | Manter chamadas serializadas dentro de cada `analyze_auto_cuts_task` filha (fila `transcription`/análise já isolada). Avaliar `rate_limit` no Celery se necessário.       |
| **Custo maior por múltiplas chamadas Grok**                 | Métrica `grok_cost_usd_total` já existe; criar alerta se custo médio por submit ultrapassar X. Documentar para o usuário que N brands = N chamadas LLM (esperado).        |
| **Inconsistência entre factories/brands**                    | `target_brand_id` força destino — sem dependência de `theme_category` da factory. `allowed_theme_categories` por análise filha continua isolado.                          |
| **Problemas de permissão** (usuário escolhe brand alheia)   | Validação no `POST /multiple-creator/` (mesmo padrão do endpoint `auto-cuts/`); 403 se faltar permissão em qualquer brand selecionada.                                     |
| **Reaproveitamento incorreto da transcrição**               | Curto-circuito em `analyze_auto_cuts_task` é gatilhado **apenas** quando a filha tem campo de origem multi-creator setado (flag explícita); jobs antigos continuam transcrevendo do zero. |
| **Conflitos com fluxo atual de criação de cortes**          | Nenhuma alteração em `CortesAutomaticos.jsx`, em `AutoCutAnalysisViewSet.create`, ou em `_resolve_target_brand_for_suggestion`. Mudanças são aditivas.                     |
| **Aumento de complexidade operacional**                     | Documento operacional curto pós-rollout: como ler logs do `MultipleCreatorJob`, como fazer retry por brand. Mantemos `correlation_id` para correlacionar.                 |
| **Arquivo de vídeo compartilhado entre filhas**             | Filhas reaproveitam o **mesmo arquivo** (FK a `SourceVideo` ou path em `MultipleCreatorJob.file`). Nunca duplicar. Não remover o arquivo antes de todas as filhas finalizarem (sinal para limpeza no `multiple_creator_completed`).  |
| **Banco — paginação por bucket pode quebrar clientes antigos** | Quando `bucket` está ausente, manter comportamento legado intacto.                                       |
| **Mudança de `page_size` afeta downloads de chamada paginada**| Usar `page_size` por chamada (querystring), respeitar `max_page_size=100`, **não** mudar default global. |

---

## 10. Roadmap incremental sugerido

### Fase 1 — Planejamento e mapeamento (este documento) — **sem código**
Pronto.

### Fase 2 — Banco de Vídeos: separação visual e paginações independentes
- Backend: adicionar suporte a `bucket=awaiting|posted` em `VideoInventoryItemViewSet.get_queryset` (Opção A da seção 3.2).
- Frontend: refatorar `BancoVideos.jsx` para duas chamadas/estados, dois `PaginationControls`.
- Testes backend + validação manual.
- **Critério de release**: o fluxo atual segue funcionando se algum cliente ainda chamar sem `bucket`.

### Fase 3 — UI inicial do Multiple-Creator (sem fluxo real)
- Nova rota `/multiple-creator`, link no menu.
- Formulário com seleção múltipla de brands e os mesmos campos do `CortesAutomaticos`.
- Submit aponta para um endpoint stub `POST /api/multiple-creator/` que retorna `501 Not Implemented` (ou um modal "em construção"). Permite validar UX sem expor backend incompleto.

### Fase 4 — Backend de orquestração
- Criar app `apps/multiple_creator/` (ou subseção em `auto_cuts/`).
- Modelos `MultipleCreatorJob` e `MultipleCreatorBrandExecution` (migration).
- Endpoint `POST /api/multiple-creator/` cria o job + executions (sem disparar tasks ainda, ou disparando uma task no-op para validar o pipeline).

### Fase 5 — Reuso de transcrição
- Implementar `multiple_creator_transcribe_task` (transcrição única, persistência em `MultipleCreatorJob.transcript_segments`).
- Adicionar curto-circuito em `analyze_auto_cuts_task` para pular transcrição quando `transcript_segments` veio pré-populado e flag de origem multi-creator está setada.

### Fase 6 — Processamento por brand (fanout + render)
- `multiple_creator_fanout_task` cria N `AutoCutAnalysis` filhas e enfileira `analyze_auto_cuts_task` por cada uma.
- Signals/callbacks fecham status de cada `BrandExecution` quando a filha conclui.
- Status agregado do job (`DONE`/`PARTIAL`/`ERROR`).
- Endpoint `POST /api/multiple-creator/<id>/retry/?brand_id=X`.

### Fase 7 — Observabilidade e testes finais
- Logs `multiple_creator_*` e métricas Prometheus.
- Testes unitários e de integração (Whisper/Grok mockados).
- Validação manual ponta a ponta (1 vídeo curto, 3 brands de 2 factories diferentes).
- Documentação operacional curta (`docs/MULTIPLE_CREATOR.md`).

---

## 11. Critérios de aceite

### Banco de Vídeos
- [ ] A página exibe "Aguardando Postagem" e "Vídeos Postados" em dois blocos visualmente separados, com paginadores independentes abaixo de cada um.
- [ ] "Aguardando" pagina com **20 itens por página**.
- [ ] "Postados" pagina com **10 itens por página**.
- [ ] Mudar página em um bloco **não** recarrega o outro.
- [ ] Filtros (`factory`, `brand`, `videoType`) continuam funcionando e resetam ambos blocos para `page=1`.
- [ ] Ações existentes (`remove-awaiting`, `retry-posting`, `mark-posted`, `download-media`) continuam funcionando.
- [ ] Clientes antigos da API que não enviam `bucket` continuam recebendo a resposta legado.

### Multiple-Creator
- [ ] Existe uma nova aba "Multiple-Creator" no menu lateral.
- [ ] O formulário permite selecionar **múltiplas brands de qualquer factory**.
- [ ] O submit gera **um** `MultipleCreatorJob` e **N** `MultipleCreatorBrandExecution` (uma por brand).
- [ ] A transcrição ocorre **uma única vez por vídeo**, validada via mock no teste e via log `multiple_creator_transcription_finished` aparecendo uma única vez por job.
- [ ] Cada brand recebe **uma chamada individual ao LLM**, validada via mock no teste e via N eventos `multiple_creator_brand_llm_finished` por job.
- [ ] Cada brand gera seus próprios `AutoCutCorte`s, com títulos/hooks/textos distintos.
- [ ] Falhas são rastreáveis por brand via `MultipleCreatorBrandExecution.status` e logs com `brand_id`.
- [ ] Falha em uma brand não impede as outras de concluírem (job final = `PARTIAL`).
- [ ] Retry granular por brand não duplica análises (uniqueness constraint).
- [ ] Logs e métricas permitem auditar o fluxo (timeline ponta a ponta via `correlation_id`).
- [ ] O fluxo atual de "Cortes Automáticos" continua funcionando sem regressões.
- [ ] O Banco de Vídeos exibe os novos itens gerados por cada brand corretamente (sem itens órfãos ou duplicados).

---

## Decisões fechadas

Histórico das 8 pendências consolidadas nas etapas de planejamento.

### Banco de Vídeos (Fase 2 — entregue)

1. **Opção A — `?bucket=awaiting|posted`** no `VideoInventoryItemViewSet`. Implementado em `apps/api/views.py` com retrocompatibilidade preservada quando o parâmetro está ausente ou desconhecido (cobertura em `VideoInventoryBucketFilterTests`).
2. **Sincronização de paginação na URL — adiada.** Estado segue só em React. Reavaliar se surgir demanda de deep-linking.
3. **Bloco "Postados" sem coluna de ações.** Decisão de produto: bloco fica read-only.

### Multiple-Creator (Fases 3-7 — a implementar)

4. **App novo `apps/multiple_creator/`.** Boundary clara entre orquestração multi-brand e o fluxo unitário de `AutoCutAnalysis`. Custo: 1 entry em `INSTALLED_APPS` + 1 migration inicial.
5. **Fechamento de `BrandExecution` via signal `post_save` em `AutoCutAnalysis`.** Quando o status da filha muda para `done`/`error`, o signal atualiza a `BrandExecution` vinculada. Sem chord/callback Celery extra.
6. **Limpeza do arquivo compartilhado: retain 24h.** Após o job ir para `DONE`/`PARTIAL`/`ERROR`, manter o arquivo por 24h para permitir retry granular reaproveitando-o. Limpeza por Celery beat diário.
7. **Custo Grok: só telemetria, sem hard limit.** Métrica `grok_cost_usd_total` ganha label `multi_creator_job_id`; alerta no Grafana quando custo médio por submit ultrapassar limiar (a calibrar com dados reais). Sem cap rígido de brands no `POST`.
8. **Sem framework de UI no frontend.** Não há Vitest/Jest configurado (`package.json` só roda `node --test` em utilitários puros). Validação do Multiple-Creator na UI será manual; lógica isolável pode ganhar testes via `node --test`.

### Lookup confirmado

- **`getBrandsAllPages()` não existe** em `frontend/src/api.js` — `getBrands(factoryId)` chama `/brands/` direto. Criar helper seguindo o padrão de `getSourcesAllPages` (usa `fetchAllListPages` já existente).
