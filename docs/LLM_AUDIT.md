# LLM Audit — social_automation

> ### ⚠ Snapshot de 2026-05-24 — não descreve o código de hoje
>
> Este documento é o insumo que originou a configuração de LLM por env vars. Boa parte das
> recomendações já virou código, e as referências de arquivo/linha abaixo são do commit
> `3c7f0e8`. Está versionado como registro da decisão, não como retrato do estado atual.
>
> **Já implementado**
>
> - Provider configurável — `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_MODEL_LIGHT`,
>   `LLM_API_KEY`, com `XAI_API_KEY` e `GROK_MODEL` mantidos só como legado e emitindo
>   warning (`3c7f0e8`). Cobre os Passos 1, 2 e 4 da seção 7 e o Alerta 1.
> - Log de redirect de modelo, comparando `response.model` com o solicitado, em
>   `apps/auto_cuts/services/grok.py` (Passo 3).
> - Corte do tamanho do output via `LLM_MAX_SHORTS` (10) e `LLM_MAX_LONGS` (5), contra os
>   30–50 shorts e 10 longs que a auditoria mediu (`3c7f0e8`). Endereça os Alertas 2 e 3.
> - Pricing por modelo com override via `GROK_PRICING_JSON` e contagem de `cached_tokens`
>   no cálculo de custo (`343acbc`). Endereça o Alerta 7.
>
> **Continua aberto**
>
> - **Alerta 4** — o retry de `analyze_chunks` repete o mesmo modelo, sem fallback.
> - **Alerta 5** — `analyze_ready_cuts_batch_titles_from_job_name` continua sem nenhum
>   chamador no repositório (verificado em 2026-08-15).
> - **Alerta 6** — não há cache de resposta do LLM.
>
> **Custos:** a seção 4 usa preços de tabela de 2026-05-24. Revalide antes de decidir
> qualquer migração com base neles.

**Data:** 2026-05-24  
**Auditor:** Claude Code (claude-sonnet-4-6)  
**Branch:** develop

---

## 1. Resumo Executivo

O projeto usa **exclusivamente xAI (Grok)** como provedor LLM, via SDK OpenAI-compatible (`openai>=1.0.0` apontando para `https://api.x.ai/v1`). O único arquivo que faz chamadas reais à API é `apps/auto_cuts/services/grok.py`. Existem **4 funções públicas** que chamam a API, todas roteando pelo ponto central `call_grok_chat()`.

O modelo configurável via `GROK_MODEL` (default `grok-4-1-fast`) recentemente pode estar sendo redirecionado pelo servidor xAI para `grok-4.3`, o que explica aumento de custo de **até 6,2x** por chamada. A chamada principal (`analyze_chunks_in_one_request`) envolve contextos **muito grandes** (transcrição completa de podcast ~25k tokens de input + ~37k tokens de output), o que torna o impacto do redirect severo.

Não existe nenhum cache LLM implementado. Retry está implementado a nível de task (3 tentativas), mas sem fallback de modelo. O código tem observabilidade completa via Prometheus. Não há chamadas a outros provedores (Anthropic, Google, OpenAI direto) em nenhuma parte do codebase.

A recomendação principal para redução de custo imediata é **fixar explicitamente `GROK_MODEL=grok-4-1-fast`** no `.env` de produção e monitorar se o redirect continua. Como alternativa de longo prazo para a operação principal (Perfil C), `gemini-2.0-flash` apresenta **custo 14% menor** com API OpenAI-compatible nativa, sem mudança de código além de `base_url` e `api_key`.

---

## 2. Inventário Completo de Chamadas

| # | Função | Arquivo | Linha | Operação (label) | Modelo | Tipo de Tarefa | Input size | Output size | Streaming | Tool calling | Visão | Raciocínio | Sync/Async | Cache | Retry |
|---|--------|---------|-------|------------------|--------|---------------|-----------|------------|-----------|-------------|-------|-----------|-----------|-------|-------|
| 1 | `analyze_chunks_in_one_request` | `apps/auto_cuts/services/grok.py` | 1638 | `analyze_chunks` | `$GROK_MODEL` (default `grok-4-1-fast`) | Extração JSON estruturada: identificar e ranquear cortes virais em transcrição completa | Grande (>25k tok: sistema ~700 + template ~1500 + transcrição ~25k) | Grande (~37k tok: 30-50 shorts + 10 longs com campos completos) | Não | Não | Não | Não | Celery task async (`analyze_auto_cuts_task`) | Não | Sim (3 tentativas, sem fallback) |
| 2 | `analyze_ready_cut_metadata` | `apps/auto_cuts/services/grok.py` | 1800 | `ready_cut_metadata` | `$GROK_MODEL` (default `grok-4-1-fast`) | Extração JSON simples: título, thumbnail_text, virality_score, timestamp | Pequeno (~2.3k tok: sistema ~290 + transcrição até 8k chars truncada ~2k tok) | Pequeno (~50 tok: 4 campos JSON) | Não | Não | Não | Não | Celery task async (`analyze_auto_cuts_task`) | Não | Não (sem retry próprio) |
| 3 | `analyze_ready_cuts_batch_titles_from_transcripts` | `apps/auto_cuts/services/grok.py` | 1849 | `ready_cuts_titles_from_transcripts` | `$GROK_MODEL` (default `grok-4-1-fast`) | Geração de títulos em lote para vários vídeos | Médio (~15k tok: sistema ~200 + N transcrições ~12k chars cada) | Pequeno (~100 tok: `{"titles": {"0": "...", "1": "..."}}`) | Não | Não | Não | Não | Celery task async (`analyze_auto_cuts_task`) | Não | Não (sem retry próprio) |
| 4 | `analyze_ready_cuts_batch_titles_from_job_name` | `apps/auto_cuts/services/grok.py` | 1889 | `ready_cuts_titles_from_job_name` | `$GROK_MODEL` (default `grok-4-1-fast`) | Geração criativa de N títulos a partir de um nome de tema | Pequeno (~100 tok: sistema ~200 + user ~50 tok) | Pequeno (~200 tok: lista de N títulos) | Não | Não | Não | Não | Celery task async (`analyze_auto_cuts_task`) | Não | Não (sem retry próprio) |

**Observações:**
- Toda chamada usa `response_format={"type": "json_object"}` com fallback sem `response_format` se a API rejeitar.
- A chamada #1 é disparada de `analyze_auto_cuts_task` (linha 1170 de `tasks.py`), com loop de retry de 3 tentativas.
- As chamadas #2 e #3 são disparadas de `_process_ready_cuts_flow` e `_process_ready_cuts_batch_flow` (linhas 498, 692, 703 de `tasks.py`).
- A chamada #4 não é usada em `tasks.py` — existe em `grok.py` mas não há import dela nos tasks ativos (função disponível mas não consumida pelo pipeline principal).

---

## 3. Agrupamento por Perfil

### Perfil A — Rápido e barato (output curto, sem raciocínio complexo)
- **Chamada #4** (`analyze_ready_cuts_batch_titles_from_job_name`): input ~100 tok, output ~200 tok. Geração criativa simples de títulos com base num nome.
- **Chamada #2** (`analyze_ready_cut_metadata`): input ~2.3k tok, output ~50 tok. Extração de 4 campos a partir de transcrição curta.

### Perfil B — Qualidade equilibrada (output médio, algum raciocínio)
- **Chamada #3** (`analyze_ready_cuts_batch_titles_from_transcripts`): input ~15k tok, output ~100 tok. Análise de múltiplas transcrições para gerar títulos apropriados ao contexto.

### Perfil C — Alta qualidade / raciocínio (output longo, complexo)
- **Chamada #1** (`analyze_chunks_in_one_request`): input ~27k tok, output ~37k tok. Análise completa de uma transcrição longa para identificar, ranquear e formatar até 60 cortes com metadados completos. Esta é a operação central e mais cara do sistema.

### Perfil D — Especializado
- Nenhuma chamada atual se encaixa aqui (não há tool calling, visão, contexto >128k, nem fine-tuning).

---

## 4. Estimativa de Custo Atual vs Alternativas

### 4.1 Parâmetros de estimativa

| Chamada | Input tokens | Output tokens | Volume estimado (calls/mês) |
|---------|-------------|--------------|--------------------------|
| `analyze_chunks_in_one_request` | ~27,302 tok | ~37,000 tok | 50 (1 video 2h/dia × 25 dias úteis) |
| `analyze_ready_cut_metadata` | ~2,289 tok | ~50 tok | 200 (4 ready-cuts/dia × 50 dias/mês) |
| `analyze_ready_cuts_batch_titles` | ~15,400 tok | ~100 tok | 30 (1 lote/dia × 30 dias) |
| `analyze_ready_cuts_batch_titles_from_job_name` | ~100 tok | ~200 tok | 10 (marginal) |

**Nota sobre o input de `analyze_chunks_in_one_request`:**
- System prompt (PT viral): ~670 tokens
- Context block (assunto + categorias): ~150 tokens  
- Template de chunks (PT viral): ~1,482 tokens
- Transcrição completa de podcast 2h (~100k chars): ~25,000 tokens
- **Total input: ~27,302 tokens**

**Nota sobre o output:**
- 50 candidate_shorts × ~600 tokens por item = 30,000 tokens
- 10 final_long_cuts × ~700 tokens por item (inclui chapters + first_comment) = 7,000 tokens
- **Total output: ~37,000 tokens**

### 4.2 Custo por chamada — `analyze_chunks_in_one_request` (Perfil C)

| Modelo | Input $/M | Output $/M | $/call | $/mês (50 calls) | Ratio vs fast |
|--------|-----------|-----------|--------|-----------------|--------------|
| **grok-4-1-fast (atual)** | $0.20 | $0.40 | **$0.0203** | **$1.01** | 1.0x |
| grok-4.3 (redirect suspeito) | $1.25 | $2.50 | $0.1266 | $6.33 | 6.2x |
| **gpt-4o-mini** | $0.15 | $0.60 | $0.0263 | $1.31 | 1.3x |
| gpt-4o | $2.50 | $10.00 | $0.4383 | $21.91 | 21.6x |
| **gemini-2.0-flash** | $0.10 | $0.40 | **$0.0175** | **$0.88** | **0.86x** |
| gemini-2.5-pro | $1.25 | $10.00 | $0.4041 | $20.21 | 19.9x |
| claude-haiku-4-5 | $0.80 | $4.00 | $0.1698 | $8.49 | 8.4x |
| claude-sonnet-4-6 | $3.00 | $15.00 | $0.6369 | $31.85 | 31.4x |

### 4.3 Custo por chamada — `analyze_ready_cut_metadata` (Perfil A)

| Modelo | $/call | $/mês (200 calls) |
|--------|--------|-----------------|
| grok-4-1-fast | $0.00048 | $0.10 |
| grok-4.3 | $0.00299 | $0.60 |
| gpt-4o-mini | $0.00037 | $0.07 |
| gemini-2.0-flash | $0.00025 | $0.05 |
| claude-haiku-4-5 | $0.00203 | $0.41 |

### 4.4 Custo por chamada — `analyze_ready_cuts_batch_titles_from_transcripts` (Perfil B)

| Modelo | $/call | $/mês (30 calls) |
|--------|--------|-----------------|
| grok-4-1-fast | $0.00312 | $0.09 |
| grok-4.3 | $0.01950 | $0.58 |
| gpt-4o-mini | $0.00237 | $0.07 |
| gemini-2.0-flash | $0.00158 | $0.05 |

### 4.5 Custo Total Mensal Estimado

| Cenário | Custo Total |
|---------|------------|
| grok-4-1-fast (ideal) | ~**$1.20/mês** |
| grok-4.3 (redirect atual suspeito) | ~**$7.51/mês** |
| gemini-2.0-flash (recomendação) | ~**$0.98/mês** |
| gpt-4o-mini | ~**$1.45/mês** |

**Impacto de retries:** Com 3 tentativas e retry nas chamadas que falham (apenas `analyze_chunks`), o custo máximo pode atingir **3× o base** nos piores cenários.

---

## 5. Requisitos Críticos Identificados

| Chamada | Dados em tempo real | Multimodalidade | Context >128k | Tool calling | Baixa latência | JSON mode | Fine-tuning | SLA/uptime | Dados sensíveis |
|---------|-------------------|-----------------|--------------|-------------|---------------|-----------|------------|-----------|-----------------|
| `analyze_chunks_in_one_request` | **Não** | **Não** | **Não** (máx ~30k tok in + 37k out ≈ 67k tok total) | **Não** | **Não** (task async, pode levar minutos) | **Sim** (crítico — usa `json_object`, parse rigoroso) | **Não** | Importante (downtime bloqueia jobs) | Transcrição (texto não-público) |
| `analyze_ready_cut_metadata` | Não | Não | Não | Não | Não | Sim | Não | Médio | Transcrição |
| `analyze_ready_cuts_batch_titles` | Não | Não | Não | Não | Não | Sim | Não | Baixo | Transcrição |
| `analyze_ready_cuts_batch_titles_from_job_name` | Não | Não | Não | Não | Não | Sim | Não | Baixo | Nenhum |

**Conclusão de requisitos:** Nenhuma chamada exige capacidade exclusiva de um único provedor. Todos os requisitos (JSON mode, tamanho de contexto, texto puro, async) são atendidos por múltiplos provedores com API OpenAI-compatible.

---

## 6. Recomendação por Chamada

| Chamada | Modelo Atual | Recomendação | Justificativa |
|---------|-------------|-------------|--------------|
| `analyze_chunks_in_one_request` | grok-4-1-fast | **Manter grok-4-1-fast** (curto prazo) / **gemini-2.0-flash** (médio prazo) | O grok-4-1-fast é 14% mais caro que gemini-2.0-flash para este workload. Gemini tem contexto de 1M tokens, JSON mode nativo e API OpenAI-compatible sem alteração de código. Pré-requisito: validar qualidade do output JSON com o schema exigido. |
| `analyze_ready_cut_metadata` | grok-4-1-fast | **gemini-2.0-flash** ou **gpt-4o-mini** | Task simples (4 campos). Ambos são mais baratos que grok-4-1-fast. OpenAI-compatible nativo. Risco quase zero de regressão. |
| `analyze_ready_cuts_batch_titles_from_transcripts` | grok-4-1-fast | **gemini-2.0-flash** ou **gpt-4o-mini** | Geração de títulos criativos. Contexto médio. Ambos têm qualidade suficiente. |
| `analyze_ready_cuts_batch_titles_from_job_name` | grok-4-1-fast | **gemini-2.0-flash** ou **gpt-4o-mini** | Input minúsculo. Qualquer modelo cheap funciona bem. |

**Recomendação de ação imediata (risco zero, impacto imediato):**  
Verificar se `GROK_MODEL` está explicitamente definido como `grok-4-1-fast` no `.env` de produção. Se o xAI está redirecionando chamadas sem esse modelo fixo para `grok-4.3`, apenas setar a variável resolve o aumento de custo de 6.2x.

**Recomendação para médio prazo (baixo risco, 14% de economia adicional):**  
Migrar para `gemini-2.0-flash` via endpoint OpenAI-compatible do Google. Requer apenas alterar `base_url` e `api_key`, sem mudança de código nos prompts ou lógica de parsing.

---

## 7. Plano de Migração Sugerido (menor ao maior risco)

### Passo 1 — Ação imediata, risco zero
**Fixar o modelo no `.env` de produção:**
```
GROK_MODEL=grok-4-1-fast
```
Se o redirect para grok-4.3 está acontecendo porque a variável não está setada (usando o default do código que já diz `grok-4-1-fast`), o problema pode ser que a variável de ambiente não está propagada para o worker Celery. Verificar:
```bash
# No worker Celery, confirmar que a variável está ativa:
python -c "import os; print(os.getenv('GROK_MODEL'))"
```

### Passo 2 — Diagnóstico de redirect (risco zero)
Monitorar as métricas Prometheus já implementadas. O counter `grok_tokens_total{model="grok-4.3"}` vs `grok_tokens_total{model="grok-4-1-fast"}` revelará se o redirect está acontecendo. O dashboard `dashboard_06_cost.json` já está configurado para isso.

Alternativamente, ativar `GROK_SAVE_RESPONSE_JSON=1` para inspecionar o campo `model` na resposta da API (`response.model` retornado pela xAI).

### Passo 3 — Adicionar log do modelo efetivamente usado (esforço baixo)
Modificar `_execute_grok_chat_completion` para logar `response.model` (o modelo que a API realmente usou, não o solicitado):
```python
# Em _execute_grok_chat_completion, após a chamada:
actual_model = getattr(response, 'model', model_name)
if actual_model != model_name:
    logger.warning("[FLUXO/Grok] Model redirect detected: requested=%s, used=%s", model_name, actual_model)
```
Isso confirma definitivamente se o redirect está ocorrendo.

### Passo 4 — Migrar chamadas Perfil A para gemini-2.0-flash ou gpt-4o-mini (esforço baixo, risco baixo)
Afeta apenas `analyze_ready_cut_metadata`, `analyze_ready_cuts_batch_titles_from_transcripts` e `analyze_ready_cuts_batch_titles_from_job_name`. Como o output é simples (4 campos, lista de títulos), o risco de regressão é mínimo.

**Mudança necessária no código:**
```python
# Opção A: gpt-4o-mini (nenhuma dependência nova)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url="https://api.openai.com/v1")
model_name = os.getenv("OPENAI_MODEL_LIGHT", "gpt-4o-mini")

# Opção B: gemini-2.0-flash via endpoint OpenAI-compatible
client = OpenAI(api_key=os.getenv("GOOGLE_AI_API_KEY"), base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
model_name = os.getenv("GEMINI_MODEL_LIGHT", "gemini-2.0-flash")
```
O restante do código (chamadas, parsing, métricas) permanece idêntico.

**Melhor abordagem:** Tornar o cliente configurável via variáveis de ambiente:
```python
LLM_PROVIDER=xai|openai|google
LLM_BASE_URL=https://api.x.ai/v1
LLM_API_KEY=...
LLM_MODEL=grok-4-1-fast
LLM_MODEL_LIGHT=grok-4-1-fast  # para chamadas Perfil A
```

### Passo 5 — Migrar chamada Perfil C (analyze_chunks) para gemini-2.0-flash (esforço médio, risco médio)
Esta é a operação crítica. O risco principal é **regressão na qualidade do JSON gerado** (campos obrigatórios ausentes, theme_category inválido, timestamps inventados).

**Procedimento de validação:**
1. Executar 10–20 análises em paralelo com grok-4-1-fast e gemini-2.0-flash no mesmo vídeo.
2. Comparar: `len(candidate_shorts)`, `len(final_long_cuts)`, taxa de `theme_category` inválido, frequência de retry.
3. Verificar se `subtitle_segments_pt` (usado no prompt `viral_translate`) mantém qualidade de tradução.
4. Só fazer o switch se taxa de retry e invalidações ficarem iguais ou menores.

**Mudança necessária:**
```python
# call_grok_chat() — apenas 3 linhas mudam:
client = OpenAI(api_key=os.getenv("GOOGLE_AI_API_KEY"), base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
model_name = os.getenv("GROK_MODEL", "gemini-2.0-flash")
# Nota: renomear a env var para LLM_MODEL seria mais limpo
```

**Métricas de custo esperadas:** $0.88/mês vs $1.01/mês (14% de economia). A economia é modesta — o principal argumento é a estabilidade de preço e a ausência de risco de redirect.

### Passo 6 — Implementar cache de resultados (esforço médio, alto impacto)
Para `analyze_ready_cut_metadata` e `analyze_ready_cuts_batch_titles_from_transcripts`, o output é determinístico dado o mesmo input. Um cache simples por hash da transcrição + modelo pode evitar 30-40% das chamadas em workflows de retry.

```python
import hashlib
from django.core.cache import cache

def _cache_key(system: str, user: str, model: str) -> str:
    return "grok:" + hashlib.sha256(f"{model}:{system}:{user}".encode()).hexdigest()[:32]
```

---

## 8. Pontos de Atenção / Alertas

### ALERTA 1 — Variável GROK_MODEL pode não estar chegando ao worker Celery
O código usa `os.getenv("GROK_MODEL", "grok-4-1-fast")` em `call_grok_chat()`. Se o `.env` não estiver sendo carregado no contexto do worker Celery (apenas no processo Django), a variável retornaria `grok-4-1-fast` mas o server xAI pode interpretar requisições sem `model` explícito (ou com modelo descontinuado como `grok-4-1-fast-reasoning`) como redirect para `grok-4.3`. **Verificar imediatamente com `os.getenv` dentro de um `shared_task`.**

### ALERTA 2 — Custo de output domina 73% do custo total (analyze_chunks)
Com grok-4-1-fast: input=$0.0055, output=$0.0148 por chamada. Com grok-4.3: input=$0.0341, output=$0.0925. A estratégia de **reduzir o tamanho do output** (ex: solicitar apenas 20 shorts em vez de 50, eliminar `ranked_shorts` da resposta) poderia reduzir o custo de output em até 40% sem troca de modelo.

### ALERTA 3 — `ranked_shorts` gerado mas pouco usado
O prompt solicita `candidate_shorts` (30-50 itens), `ranked_shorts` (pode ser vazio), e `final_long_cuts` (10 itens). O código em `tasks.py` (linha 1232) usa **apenas** `candidate_shorts` para o prompt viral (o maior pool), e `ranked_shorts` como fallback. Solicitar explicitamente `ranked_shorts: []` no prompt poderia cortar ~15-20% do output.

### ALERTA 4 — Sem fallback de modelo em caso de erro
O retry de 3 tentativas em `analyze_chunks` repete o mesmo modelo. Se o problema for de quota/limite do modelo, tentativas adicionais são inúteis. Seria útil implementar fallback para um modelo alternativo na 3ª tentativa.

### ALERTA 5 — `analyze_ready_cuts_batch_titles_from_job_name` não está sendo usada nos tasks
A função existe em `grok.py` e foi importada no histórico de commits mas não aparece em nenhum import ativo de `tasks.py`. Pode ser código morto — verificar se há outra entry point chamando essa função ou se pode ser removida para simplificar.

### ALERTA 6 — Sem cache LLM implementado
Qualquer retry (por falha ou re-análise manual) gera custo integral. Para `analyze_ready_cut_metadata` em particular, o output é quase sempre o mesmo para a mesma transcrição. Um cache Redis de 24h reduziria custos em re-análises.

### ALERTA 7 — GROK_PRICING_JSON permite override via env var, mas pricing do grok-4.3 não está cadastrado
Se o modelo for redirecionado para `grok-4.3` pelo servidor, as métricas de custo em Prometheus ficarão incorretas (o pricing de `grok-4.3` não existe em `GROK_PRICING`). O custo real pode estar sendo subnotificado. Para corrigir:
```
GROK_PRICING_JSON={"grok-4.3": {"input_per_1k": 0.00125, "output_per_1k": 0.0025}}
```

### ALERTA 8 — Context window: sem risco imediato, mas próximo do limite para podcasts muito longos
Input ~27k tokens + output ~37k tokens = ~64k tokens totais. O grok-4-1-fast tem context window de 131k tokens. Podcasts muito longos (>4h) ou com sobreposição maior podem pressionar esse limite. gemini-2.0-flash tem 1M tokens, eliminando completamente esse risco.

---

## Referências de Arquivo

> Números medidos em 2026-05-24. O plano de refatoração mudou os dois primeiros: hoje
> `grok.py` tem 897 linhas (os prompts saíram no R-18) e `apps/auto_cuts/tasks.py` tem 73
> (o corpo virou serviços). As linhas citadas nas seções 2 e 8 não valem mais.

- Principal: `apps/auto_cuts/services/grok.py` (1923 linhas) — toda a lógica LLM
- Celery tasks: `apps/auto_cuts/tasks.py` (2110 linhas) — orchestração das chamadas
- Métricas: `apps/common/metrics.py` — observabilidade Prometheus
- Configuração: `.env.example`, `social_automation/settings.py`
- Testes: `apps/auto_cuts/tests/test_grok_observability.py`
- Dashboard Grafana de custo: `monitoring/grafana/dashboards/dashboard_06_cost.json`
