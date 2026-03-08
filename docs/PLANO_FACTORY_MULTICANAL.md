# Plano Técnico: Factory Multicanal (Shorts + Longos)

## Objetivo

Implementar uma camada de operação chamada `Factory` para gerenciar múltiplas `Brands` (canais) com:

- geração automatizada de cortes por categoria;
- banco de vídeos por brand (shorts e longos);
- agendamento diário inteligente por regras de janela, intervalo e limites;
- publicação com retries e logs para debug;
- histórico de vídeos postados para analytics futuro.

Este documento descreve **apenas planejamento técnico**, sem implementação.

---

## Regras de negócio já definidas

1. `Factory` terá várias `Brands` (entre 1 e 10 por factory).
2. Categorias de tema (fixas):
   - `BUSINESS_MONEY` (Negócios / Dinheiro)
   - `PSYCHOLOGY_RELATIONSHIPS` (Psicologia / Relacionamentos)
   - `STORIES_CURIOSITIES` (Histórias e Curiosidades)
   - `CONTROVERSIES_DEBATE` (Polêmicas / Debate)
   - `COMEDY_HUMOR` (Comédia / Humor)
3. Relação categoria -> brand é **1:1** (uma categoria alimenta uma única brand).
4. Slot de short aceita apenas short; slot de longo aceita apenas longo (não compensa um pelo outro).
5. `Factory` terá timezone padrão (ex.: BR e US no futuro).
6. Retry de publicação:
   - 3 tentativas;
   - intervalo de 5 minutos;
   - se falhar, não trava pipeline, segue para próximo item.
7. Publicação em lotes pequenos/serial por canal (evitar burst único na API).
8. Critério de diversidade usa `source_asset_id` (ID do vídeo original):
   - evitar mais de 2 posts seguidos com mesmo `source_asset_id`;
   - fallback permitido quando não houver estoque diversificado.
9. Após publicar com sucesso:
   - apagar mídia física local;
   - manter log e metadados para analytics futuro.

---

## Escopo funcional

### 1) Contexto Factory na UI

Adicionar seletor de `Factory` no topo da aplicação (junto ao contexto de brand atual).

Ao selecionar uma factory, menu lateral deve mostrar:

- `Dashboard` (futuro; escopo desta fase é estrutural)
- `Brands`
- `Criação de Cortes`
- `Banco de Vídeos`
- `Agendamentos`
- `Vídeos Postados`

### 2) Brands dentro da Factory

No cadastro/edição da brand, incluir:

- `name` (nome do canal de postagem);
- `theme_category` (categoria fixa da brand);
- `logo` (arquivo);
- `thumbnail_font_family`;
- `thumbnail_band_color`;
- `thumbnail_text_color`;
- `thumbnail_effect_color`;
- `description_suffix` (texto complementar da descrição);
- `min_short_interval_minutes`;
- `min_long_interval_minutes`;
- `max_shorts_per_day`;
- `max_longs_per_day`;
- `short_window_start` / `short_window_end`;
- `long_window_start` / `long_window_end`.

### 3) Criação de cortes com roteamento automático

Fluxo mantém base atual (transcrição -> LLM/xAI -> cortes), mas passa a exigir:

- retorno obrigatório de `theme_category` no payload da LLM;
- validação estrita: somente uma das 5 categorias aceitas;
- gravação de metadado `theme_category`;
- roteamento direto do corte para a brand correspondente.

### 4) Finalização de mídia por tipo

- Short:
  - enquadramento com zoom (quando aplicável ao fluxo atual);
  - capa sem logo (apenas faixa + título com preset da brand).
- Longo:
  - finalização padrão;
  - legenda se selecionada;
  - logo da brand no topo esquerdo (`50x50 px`).

### 5) Banco de vídeos por brand

Criar lista operacional por brand e por tipo:

- aba `Shorts`
- aba `Longos`

Cada item deve guardar, no mínimo:

- status (`AVAILABLE`, `SCHEDULED`, `POSTING`, `POSTED`, `FAILED`, `RETRY_WAIT`);
- score/rank de viralização;
- `source_asset_id`;
- origem da análise/job/corte;
- timestamps e metadados de publicação.

### 6) Agendamento diário automático

Todos os dias às 11:00 (timezone da factory), para cada brand:

1. ler estoque disponível no banco de vídeos;
2. gerar agenda de shorts dentro da janela de shorts;
3. gerar agenda de longos dentro da janela de longos;
4. respeitar:
   - máximo por dia por tipo;
   - intervalo mínimo por tipo;
   - janela por tipo;
5. se não couber item no fim da janela, manter para próximo dia;
6. não usar horários já passados caso um item entre no mesmo dia após geração inicial.

### 7) Ordem de postagem

Regras combinadas:

1. ordenar candidatos por score crescente (menor -> maior), mantendo melhores para o fim da janela;
2. aplicar diversidade por `source_asset_id`:
   - evitar sequência >2 iguais;
   - fallback quando não houver diversidade suficiente.

### 8) Publicação e retries

Executor da fila de agendamento:

- processa por brand/canal em lote pequeno (serial por canal);
- para cada item:
  - tenta publicar;
  - em caso de erro temporário, agenda retry em 5 min;
  - máximo 3 tentativas;
  - ao exceder, marca `FAILED` e segue.

Registrar logs por tentativa no menu `Agendamentos` para facilitar debug.

---

## Modelo de dados (proposta)

## Factory

- `id`
- `name`
- `timezone` (IANA, ex.: `America/Sao_Paulo`, `America/New_York`)
- `is_active`
- `created_at`, `updated_at`

## Brand (extensão)

- `factory_id` (FK)
- `theme_category` (enum único)
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

Restrições:

- unicidade de `theme_category` por `factory` (garante regra 1:1 dentro da factory);
- validações de janela (start < end);
- intervalos e limites >= 0.

## VideoInventoryItem (novo)

- `id`
- `factory_id`
- `brand_id`
- `video_type` (`SHORT`, `LONG`)
- `file_path` (ou ponteiro para storage)
- `title`
- `description`
- `viral_score`
- `source_asset_id` (ID do vídeo original)
- `source_metadata` (json opcional)
- `origin_job_id` / `origin_cut_id` (nullable)
- `status`
- `scheduled_for` (nullable)
- `posted_at` (nullable)
- `attempt_count`
- `last_error`
- `created_at`, `updated_at`

## PostingSchedule (novo)

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

## PostingAttemptLog (novo)

- `id`
- `posting_schedule_id`
- `attempt_number`
- `started_at`, `finished_at`
- `result` (`SUCCESS`, `ERROR`)
- `error_code`, `error_message`
- `provider_response` (json opcional)

## PostedVideoLog (novo)

- `id`
- `factory_id`
- `brand_id`
- `video_inventory_item_id`
- `external_platform` (ex.: YT/YTB)
- `external_video_id`
- `posted_at`
- `metadata_snapshot` (json)

---

## Contrato LLM/xAI (ajuste obrigatório)

Adicionar campo obrigatório no retorno:

```json
{
  "theme_category": "BUSINESS_MONEY"
}
```

Validação:

- se vazio ou inválido: retry da etapa de classificação;
- após N falhas (definir em config), marcar corte como erro de classificação.

Observação: esse campo pode ser incluído em todas as requisições sem impactar fluxos atuais que ainda não o consomem.

---

## Algoritmo de agendamento diário (pseudo)

```text
for each factory (ativa):
  now_factory_tz = now in factory.timezone
  if hora_local == 11:00:
    for each brand da factory:
      gerar_slots_short(brand.short_window, brand.min_short_interval, brand.max_shorts_per_day)
      gerar_slots_long(brand.long_window, brand.min_long_interval, brand.max_longs_per_day)

      candidatos_short = inventory AVAILABLE + tipo SHORT + brand
      candidatos_long  = inventory AVAILABLE + tipo LONG + brand

      ordenar por viral_score asc
      aplicar diversidade por source_asset_id (evitar >2 iguais seguidos; fallback se necessário)

      preencher slots com candidatos:
        - respeitar janela e intervalos
        - não usar horários passados no dia corrente
        - se não couber, manter item como AVAILABLE para próximo ciclo

      criar PostingSchedule status=PLANNED
      marcar inventory como SCHEDULED quando vinculado
```

---

## Algoritmo de publicação (pseudo)

```text
selecionar schedules PLANNED com scheduled_at <= now
ordenar por brand (lote por canal) e scheduled_at

para cada schedule:
  tentar publicar (1 tentativa por vez)
  se sucesso:
    marcar schedule DONE
    marcar inventory POSTED
    gravar PostedVideoLog
    deletar mídia física
  se erro temporário e attempt_count < 3:
    attempt_count += 1
    schedule.next_retry_at = now + 5 min
    status = PLANNED (ou RETRY_WAIT)
  se erro final:
    status = FAILED
    inventory = FAILED (ou AVAILABLE, conforme política futura)
    seguir próximo sem bloquear fila

sempre gravar PostingAttemptLog
```

---

## Menus e telas (MVP)

1. `Factory Selector` (topbar)
2. `Brands` (CRUD + regras de agenda + visual)
3. `Criação de Cortes` (fluxo atual com category routing)
4. `Banco de Vídeos` (fila por brand/tipo)
5. `Agendamentos` (lista do dia + status + retries + erros)
6. `Vídeos Postados` (histórico para analytics futuro)

`Dashboard` fica reservado para fase posterior.

---

## Estratégia de implementação por sprint

### Sprint 1 — Base de dados e administração

- criar entidade `Factory`;
- vincular `Brand` à factory;
- adicionar campos novos na brand;
- constraints de categoria 1:1 por factory;
- CRUD básico de factory/brand no admin.

**Aceite**
- criar factory com timezone;
- criar 1..10 brands com categorias únicas;
- salvar regras de janela/intervalo/limites.

### Sprint 2 — Classificação e roteamento

- ajustar contrato LLM para `theme_category`;
- validar enum obrigatório;
- roteamento de cortes para brand por categoria;
- criação dos itens em `VideoInventoryItem`.

**Aceite**
- corte gerado com categoria válida cai no banco da brand correta;
- categoria inválida gera erro controlado.

### Sprint 3 — Finalização de mídia por brand

- aplicar presets de thumbnail/descrição;
- regras short vs longo;
- logo em longos `50x50` no topo esquerdo.

**Aceite**
- output visual respeita preset da brand;
- short sai sem logo; longo com logo.

### Sprint 4 — Agendador diário

- job diário 11:00 por timezone da factory;
- geração de slots por tipo;
- ordenação por score asc;
- diversidade por `source_asset_id`;
- não usar horários já passados.

**Aceite**
- agenda do dia criada corretamente;
- itens excedentes ficam para dia seguinte.

### Sprint 5 — Executor de publicação + retry + logs

- execução em lote serial por canal;
- retry 3x/5min;
- logs detalhados de tentativa;
- não bloquear fila em falha.

**Aceite**
- erro em item não interrompe os demais;
- logs completos disponíveis na tela de agendamentos.

### Sprint 6 — Pós-publicação e histórico

- gravar `PostedVideoLog`;
- remover mídia local após sucesso;
- tela de vídeos postados por brand.

**Aceite**
- mídia é removida apenas após sucesso;
- histórico preserva metadados para analytics futuro.

---

## Riscos e mitigação

1. **Burst/API limit**
   - mitigar com lotes pequenos e serial por canal.
2. **Classificação inconsistente da LLM**
   - enum fechado + validação estrita + retry da classificação.
3. **Conflito de timezone/DST**
   - salvar timezone IANA na factory e converter sempre no backend.
4. **Falta de estoque por tipo**
   - manter slot vazio por regra de negócio e registrar motivo.
5. **Baixa diversidade de origem**
   - fallback explícito quando não há alternativa para `source_asset_id`.

---

## Critérios de aceite globais (MVP)

1. Operar múltiplas factories com timezone independente.
2. Operar múltiplas brands por factory com categoria única.
3. Gerar e rotear cortes por `theme_category` obrigatório.
4. Manter banco de vídeos por brand/tipo com estados rastreáveis.
5. Agendar diariamente respeitando janelas, limites e intervalos.
6. Publicar com retry 3x/5min sem bloquear pipeline.
7. Registrar tentativas e falhas para debug.
8. Preservar histórico de postados e remover mídia local após sucesso.

---

## Fora do escopo desta fase

- dashboard avançado de métricas e analytics;
- otimização por performance histórica de horários;
- recomendação automática de pauta por learning loop.

