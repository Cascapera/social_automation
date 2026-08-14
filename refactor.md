# Projeto de Refatoração — social_automation

| | |
|---|---|
| **Repositório** | `social_automation` |
| **Branch analisada** | `develop` (25 commits à frente de `master`, contém `master` inteiro) |
| **Commit base** | `3c7f0e8` — 2026-05-24 |
| **Data da análise** | 2026-08-11 |
| **Escopo** | global (não informado) |
| **Foco** | global (não informado) |
| **Itens que exigem parada de produção** | **nenhum** |

---

## ⏸ PONTO DE RETOMADA — sessão de 2026-08-14

**Onde paramos:** **o R-19 está fechado.** Os quatro PRs saíram no mesmo dia:
(a) CT-4, (b) `analyze_auto_cuts_task`, (c) `finalizar_auto_cut_task` e (d) os imports
dentro de função.

`apps/auto_cuts/tasks.py` foi de **2.111 para 73 linhas** — as duas tasks Celery e nada
mais. O corpo virou três módulos em `services/`:

```
tasks.py                só as tasks, que delegam (o nome é contrato de fila)
  ↓
analysis_flow.py        run_analysis + 7 etapas + os fluxos de cortes prontos
finalization_flow.py    run_finalization + _process_cut + 6 etapas de render
  ↓
flow_common.py          os helpers que os dois fluxos usam
```

Saíram também **dois lotes do R-17**: o lote 2 (`WHISPER_*`, 5 leitores — com a surpresa
que virou o **L-12**: a mesma variável tinha dois defaults diferentes) e o lote 3
(`LLM_*`/`XAI_*`/`GROK_*`, 11 variáveis). Restam ~34 `os.getenv` para o lote 4.

Os dois arquivos de CT-4 passaram **sem mudar nenhuma asserção** — só os alvos de `patch()`
acompanharam o código de módulo. Era exatamente para isso que o PR (a) foi escrito antes.
Suíte em **416 testes**, lint verde, cobertura local **44,04%**.

### ▶ NA PRÓXIMA SESSÃO, NESTA ORDEM

1. **R-08** — libera em **2026-08-15**, 48h depois do deploy de 13/08. Movimentação pura,
   risco baixo: mover `_resolve_*` e afins de `apps/social/tasks.py` para
   `services/publish_targets.py`.
2. **Subir a catraca de cobertura logo depois do R-08**, com o número do log do **CI**
   (não o local). O piso segue em 42,0 e a margem já passou de 1,7pp.
3. Depois disso, a onda 1 continua no **R-09**.

> **4 itens esperando deploy**: R-19 (b), R-19 (c)+(d), R-17 lote 2 e R-17 lote 3 — os
> quatro transparentes. A verificação pós-deploy de cada um está na tabela da seção de
> pendências. **O lote 3 tem uma pegadinha de deploy**: `LLM_MAX_SHORTS`/`_LONGS` com
> valor inválido agora impedem o boot em vez de falhar por análise. Se o `.env` de
> produção tiver lixo nessas duas, o deploy falha — o que é o objetivo, mas convém
> conferir antes de subir.

### Estado do R-19 — ✅ concluído (falta deploy)

- ✅ **(a) CT-4** — 21 characterization tests das duas tasks gigantes (#40)
- ✅ **(b)** `analyze_auto_cuts_task` → `services/analysis_flow.py`
- ✅ **(c)** `finalizar_auto_cut_task` → `services/finalization_flow.py`
- ✅ **(d)** imports dentro de função removidos (3 ficaram, com o motivo no código)

> ⚠ **O que o R-19 preservou de propósito, e que a próxima pessoa não deve "melhorar":**
> falha de finalização **não** vira `status="error"` — a análise fica parada em
> `finalizing` porque o recovery procura esse estado; as mensagens de erro são contrato de
> tela (duas delas pedem ações opostas do usuário); e cada etapa de render engole a própria
> exceção, de modo que uma etapa que falha não aborta as seguintes.

### Achados desta sessão que não estavam no plano

| O que | Onde apareceu | Situação |
| --- | --- | --- |
| `ALL_THEME_CATEGORIES` existe em duas cópias idênticas | R-19 (b) | registrado · **L-11** · PR próprio (a de `prompts/` está congelada por hash) |
| `_mark_analysis_done` tem uma segunda cópia em `recovery.py` | R-19 (b) | registrado · **L-11** — mesma lógica, sem a guarda de linha apagada |
| `horizontal_insert_logo` atravessa a fila e ninguém lê | R-19 (c) | terceira ocorrência do **L-9** — mantido na assinatura, sinalizado no docstring |
| `DEFAULT_SUBTITLE_STYLE` (o alias) não tem leitor | R-19 (c) | idem — movido junto, não apagado |
| `has_nvenc()` roda a cada finalize, mesmo sem cortes | CT-4 | continua assim; agora é 1 linha isolada em `run_finalization`, fácil de memoizar |
| `WHISPER_MODEL` era lida com **dois defaults diferentes** (`small` × `large-v3`) | R-17 lote 2 | preservados nos dois caminhos · **L-12** · unificar é decisão sua |

### Achados da sessão anterior (13/08)

| O que | Onde apareceu | Situação |
| --- | --- | --- |
| `YOUTUBE_CHECK_CLIENT_ENABLED` era código morto | R-17 | removido · **L-9** |
| `CTR_WORDS_*` / `FORBIDDEN_WORDS_*` são código morto (~60 linhas) | R-18 | movidas e sinalizadas · **decisão sua pendente** |
| Congelamento de hash com escopo largo demais deixou `develop` vermelho | R-18 → #39 | corrigido · **L-10** |

### ⚠ Três coisas pendentes que dependem de você

1. **Decidir sobre `CTR_WORDS_*` / `FORBIDDEN_WORDS_*`** — ligar ao pipeline ou remover?
   Estão em `apps/auto_cuts/prompts/vocabulary.py`, declaradas e sem leitor. Ver **L-9**.
2. **Seu `.env.example` tem alteração local não commitada** (`# LLM_API_KEY=AIza...` para
   vazio). Ela foi preservada nos dois `pull` desta sessão, mas continua fora do git —
   commitar ou descartar.
3. **A decisão de processo do L-7**: congelar features em `apps/social/` durante o resto
   da onda 1, ou aceitar rebases?

---

## Registro da sessão anterior — 2026-08-13 (manhã)

**Onde paramos:** ✅ **O D-02 está fechado.** As 4 decisões do L-7 foram tomadas e o R-07
mergeado — a máquina de estados de publicação tem **um dono só**, e um teste no CI impede
que nasça uma sexta cópia. Suíte de 288 → **365 testes**.

> **Este era o item que paga o projeto.** O resumo executivo dizia que a onda 1 "elimina a
> duplicação da máquina de estados e torna as transições atômicas — é onde 15 dos commits
> de `fix` nasceram". R-06 fez a atomicidade; R-07 fez a unificação. O que vem a seguir na
> onda 1 é fatiamento de função: valioso, mas não é mais sangramento.

Foram 3 PRs nesta sessão: **#36** (R-07), **#37** (R-17 lote 1) e **#38** (R-18). Na
anterior foram 4: #32, #33, #34 e #35.

Dois diagnósticos fechados hoje: **D-02** (máquina de estados duplicada) e **D-09**
(prompts dentro do cliente HTTP). Os dois maiores arquivos do projeto encolheram —
`grok.py` de 2.057 para **897 linhas**.

⚠ **Os dois itens de hoje acharam código morto que o plano tratava como vivo** — a flag do
R-17 e as listas de palavras do R-18. Está registrado em **L-9**, com a pergunta que isso
levanta sobre as outras contagens deste documento.

> ✅ **A catraca deixou de ser risco.** Trajetória no CI: 42,03 antes do R-07 → **42,01**
> depois dele (0,01pp de margem, o susto) → **42,08** com o R-17 lote 1 → **42,22** com o
> R-18 → **43,46** com o CT-4 (#40) → **43,51** com o R-19 (b) → **43,77** com o R-19
> (c)+(d) → **43,77** com o R-17 lote 2. A margem sobre o piso de 42,0 é de **1,77pp**.
>
> ⚠ O lote 2 subiu 0,25pp no local (43,76 → 44,01) e **não mexeu no número do CI**. É a
> mesma divergência já registrada aqui: `settings.py` mede 84% local contra 77% no CI,
> e os 10 testes novos batem justamente em `settings.py`. Vale como lembrete para quem
> for subir a catraca: o número é o do log do CI, não o da máquina.
>
> **Ainda não subi o piso, de propósito.** O R-08 é movimentação pura de código bem
> coberto: muda o denominador sem melhorar nada e tende a puxar o total para baixo. Subir
> a catraca antes dele garantiria build vermelho por um motivo alheio ao trabalho.
> **Subir logo depois do R-08**, com o número estabilizado.
>
> As duas subidas vieram do jeito honesto — cobrir ramo que estava intestável, não somar
> teste em módulo barato. Se apertar de novo, os próximos candidatos naturais são
> `youtube_credentials.py` (13%) e `youtube_fetch.py` (8%), que ficaram testáveis agora que
> a configuração deles responde a `override_settings`.

> ⚠ **A catraca sai do número do CI, não do local.** A suíte local lê ~0,2pp a mais
> (42,23% contra 42,03%) porque alguns ramos dependem de variáveis de ambiente e de
> dependências opcionais que diferem entre os dois — `settings.py` mede 84% local e 77% no
> CI; `upload_post_analytics_client.py`, 19% contra 11%. A primeira tentativa do R-04
> subiu a catraca para 42,2 com base na medição local e **quebrou o CI** com os 351 testes
> passando. Quem subir a catraca de novo: pegar o número do log do CI.

### Feito e mergeado

| Item | PR | Estado |
| --- | --- | --- |
| **R-01** · `_NoOpMetric.observe()` | [#25](https://github.com/Cascapera/social_automation/pull/25) | mergeado · **falta deploy** |
| **R-02** · portão de cobertura real (40,8%) | [#29](https://github.com/Cascapera/social_automation/pull/29) | ✅ concluído |
| **R-05** · testes do frontend no CI | [#27](https://github.com/Cascapera/social_automation/pull/27) | ✅ concluído |
| **R-21** · `update_fields` com campo inexistente | [#30](https://github.com/Cascapera/social_automation/pull/30) | mergeado · **falta deploy** |
| **R-03** · characterization tests (31) | [#31](https://github.com/Cascapera/social_automation/pull/31) | ✅ concluído |
| **R-04** · characterization tests (22) | [#32](https://github.com/Cascapera/social_automation/pull/32) | ✅ concluído |
| **R-22** · guarda inalcançável de "Job sem vídeo final" | [#33](https://github.com/Cascapera/social_automation/pull/33) | mergeado · **falta deploy** |
| **R-23** · `post.error` sobrevivia num post `DONE` | [#34](https://github.com/Cascapera/social_automation/pull/34) | mergeado · **falta deploy** |
| **R-06** · `posting_state.py` + transições atômicas | [#35](https://github.com/Cascapera/social_automation/pull/35) | mergeado · **falta deploy em janela** |
| **R-07** · as 5 cópias apontando para `posting_state` | [#36](https://github.com/Cascapera/social_automation/pull/36) | mergeado · **falta deploy** |
| **R-17 lote 1** · `YOUTUBE_CHECK_*` em `settings` | [#37](https://github.com/Cascapera/social_automation/pull/37) | mergeado · **falta deploy** |
| **R-18** · prompts fora do `grok.py` (2.057 → 897) | [#38](https://github.com/Cascapera/social_automation/pull/38) | ✅ implantado |
| — · escopo do congelamento de hash (L-10) | [#39](https://github.com/Cascapera/social_automation/pull/39) | ✅ concluído |
| **R-19 (a)** · CT-4 — 21 characterization tests | [#40](https://github.com/Cascapera/social_automation/pull/40) | ✅ concluído (só teste) |
| **R-19 (b)** · `analyze_auto_cuts_task` → `services/` (2.110 → 616) | [#41](https://github.com/Cascapera/social_automation/pull/41) | mergeado · **falta deploy** |
| **R-19 (c)+(d)** · `finalizar_auto_cut_task` → `services/` (616 → 73) | [#42](https://github.com/Cascapera/social_automation/pull/42) | mergeado · **falta deploy** |
| **R-17 lote 2** · `WHISPER_*` em `settings` | [#43](https://github.com/Cascapera/social_automation/pull/43) | mergeado · **falta deploy** |
| — · este documento | [#28](https://github.com/Cascapera/social_automation/pull/28) | ✅ |

### ▶ PRÓXIMO PASSO: R-08, a partir de 2026-08-15

✅ **Deploy feito em 2026-08-13.** O plano manda deixar R-06+R-07 em produção por **≥48h**
antes de seguir para o R-08 (seção 8) — são os dois itens que mexeram na atomicidade e na
unificação da transição de publicação, o ponto de maior atenção do projeto inteiro.

Contando do deploy, o R-08 libera em **2026-08-15**. Ele já está destravado do lado
técnico (pré-requisito é o R-04, feito) e é movimentação pura, de risco baixo.

> Enquanto a janela não fecha, o que não conflita com o R-08 é o **lote 2 do R-17**
> (FFmpeg / filas Celery) e o **R-19** (fatiar `auto_cuts/tasks.py`, que convive bem com o
> `prompts/` criado no R-18).

### 📌 O que fazer ao retomar

1. `git pull` em `develop` (esperado: `d7d2a59` ou mais novo).
2. Conferir a tabela de verificação pós-deploy, abaixo — a janela vai até 2026-08-14.
3. Se já passou 2026-08-15: começar o R-08 (seção 7).

### ✅ Deploy feito em 2026-08-13

R-01, R-21, R-22, R-23, R-06, R-07, R-17 lote 1 e R-18 estão em produção. **As
verificações pós-deploy abaixo continuam valendo por 24h** — e o relógio de 48h para
liberar o R-08 começou a contar a partir daqui.

### ⚠ Pendências fora do código

1. **Verificação pós-deploy — janela até 2026-08-14.** O que olhar, e o que cada número
   significa. **Movimento esperado não é regressão** — três destes itens *devem* mexer nos
   painéis:

   | O quê | Esperado | Item |
   | --- | --- | --- |
   | `ScheduledPost` `DONE` com `VideoInventoryItem` não-`POSTED` | **0** — é a inconsistência que R-06+R-07 eliminam | R-06, R-07 |
   | Deadlock no Postgres | **sem aumento** — a transação segura lock em até 4 tabelas por alguns ms | R-06 |
   | `publish_reconciliation_failures_total` | **caindo** | R-21 |
   | Itens saindo de "Aguardando Postagem" sozinhos, em lote | **pico esperado** — é a fila acumulada sendo processada, não incidente | R-21 |
   | Posts `FAILED` com motivo legível | **sobe** — eram os travados em `PENDING`; é diagnóstico ficando visível | R-22 |
   | `PostedVideoLog` novos no ramo não-YouTube | **cai** — é a duplicata sumindo, não perda de auditoria | R-07 |
   | `attempt_count` em item marcado à mão | passa a ser preenchido (antes ficava com valor velho) | R-07 |
   | `publish_duration_ms` em `/metrics` | **presente** | R-01 |
   | OAuth de factory-check | **funcionando** — é o único fluxo que depende do `redirect_uri` migrado | R-17 |
   | Custo médio por análise e taxa de retry do Grok | **estáveis** — nenhum byte enviado ao LLM mudou | R-18 |

   R-01 e R-23 são transparentes (R-23 só para de exibir erro em post que deu certo).

2. **1 decisão de processo em aberto** (não bloqueia nada): vale congelar features em
   `apps/social/` durante o resto da onda 1, ou aceitar rebases? Está no **L-7**
   (seção 15). As 4 decisões técnicas que bloqueavam o R-07 já foram tomadas.

> ~~5 erros locais só no Windows~~ — **resolvido no R-07.** O `→` (U+2192) que
> `fix_youtube_posted_status.py` escrevia em stdout levantava `UnicodeEncodeError` em
> console cp1252. A linha foi reescrita no R-07 e virou ASCII; `manage.py test` passa
> limpo no Windows agora (365 testes, OK).

### Ambiente

O `.venv` foi sincronizado com o que o projeto já declarava: `prometheus-client`,
`pytest`, `pytest-django`, `pytest-cov`. Comandos que valem hoje:

```
# suíte (rápida, sem cobertura)
DJANGO_SETTINGS_MODULE=social_automation.settings_test  manage.py test
# suíte com o portão de cobertura, igual ao CI
pytest -q
# lint
.venv/Scripts/ruff.exe check .
```

### Convenções em vigor nesta execução

- **Um item = uma branch = um PR.** Correção de bug vai em PR próprio com prefixo
  `fix()`, nunca misturada com refatoração.
- **Merge quando o CI ficar verde** (autorizado pelo usuário em 2026-08-11).
- **Não usar `--delete-branch` no merge** de uma branch que seja base de outra PR — o
  GitHub fecha a PR empilhada e ela não pode ser reaberta (aconteceu com a #26).
- Mensagem de commit/PR longa vai por arquivo (`-F` / `--body-file`): here-string no
  PowerShell deste ambiente quebra o texto.

---

> Este documento nasceu como **plano**. A partir de 2026-08-11 ele é também o registro da
> execução: a seção 13 é o checklist vivo e a seção 15 guarda o que ficou em aberto.

---

## 1. Resumo executivo

O `social_automation` é um pipeline Django + Celery de produção de cortes e publicação
automática em redes sociais. São ~34.000 linhas de Python (fora `.venv` e migrations) e
~11.800 de React. O projeto **não está mal cuidado**: tem CI, ruff zerado (0 violações),
ADRs, documentação de arquitetura, observabilidade com Prometheus e 288 testes. A base é
melhor que a média.

O problema é outro, e é bem localizado: **a intenção arquitetural documentada em
`docs/ARCHITECTURE.md` não é respeitada por 4 arquivos**, e são exatamente os arquivos que
mais mudam e que mais aparecem em commits de correção.

- `apps/social/tasks.py` — **4.035 linhas**, 42 alterações em 12 meses, **15 commits de
  `fix`** (o dobro do segundo colocado). Contém uma única função de **1.189 linhas**
  (`_run_post_to_platforms`, linha 2459).
- `apps/api/views.py` — **2.522 linhas**, 38 alterações, com regra de negócio e
  transações escritas dentro de handlers HTTP.
- `apps/auto_cuts/tasks.py` — **2.111 linhas**, 32 alterações, duas funções de 652 e 556
  linhas.
- `apps/auto_cuts/services/grok.py` — **2.057 linhas**, sendo ~1.235 de prompts e tabela
  de preços misturados com cliente HTTP e parsing.

A consequência prática, medida e não opinada: **a máquina de estados de publicação está
escrita em 5 lugares diferentes**, sem dono, e **sem transação** — `_mark_factory_posting_verified`
(`apps/social/tasks.py:823`) altera 4 modelos em sequência com zero `transaction.atomic()`.
Todo o arquivo de 4.035 linhas tem **um** `atomic` para **43** chamadas de `save()`. É por isso
que esse arquivo lidera o ranking de correções: cada bug de estado inconsistente é corrigido
em um dos 5 lugares e reaparece pelos outros quatro.

Agrava o quadro que **o portão de cobertura mede 7,5% do código**: o `--cov-fail-under=70`
do `pyproject.toml` roda sobre 12 módulos que somam 2.562 linhas — quase todos models,
urls e settings. Os 4 arquivos acima **não são medidos**. O CI fica verde exatamente onde
o risco está.

**Proposta:** 18 PRs pequenos, em 4 ondas, concentrados no fluxo de publicação primeiro
(onde está a dor mensurável) e só depois no resto. **Nenhum item exige parada de produção
nem alteração de schema.**

**Custo honesto:** ~104 horas de trabalho focado (≈ 13 dias úteis de implementação),
que na prática viram **~4 a 6 semanas** de calendário considerando revisão, deploy
faseado e o trabalho normal seguindo em paralelo.

**Ganho esperado:** a onda 1 sozinha (≈ 45h) elimina a duplicação da máquina de estados e
torna as transições atômicas — é onde 15 dos commits de `fix` nasceram. O resto é
importante, mas é conforto, não sangramento.

**Recomendação:** vale a pena, mas **só as ondas 0 e 1 são urgentes**. Se o tempo apertar,
pare depois da onda 1 e o projeto já terá pago o custo. As ondas 2–3 podem esperar sem
prejuízo.

---

## 2. Escopo desta rodada

ESCOPO e FOCO vieram vazios → **projeto global**.

### Dentro

- Todo o código Python em `apps/` e `social_automation/`.
- Configuração de teste, cobertura e CI (`pyproject.toml`, `.github/workflows/ci.yml`).
- Frontend React apenas no que diz respeito a **CI e testes** (R-05). Refatoração de
  componentes React está fora — ver seção 12.

### Fora

- **Schema do banco.** Nenhum item deste plano altera migration, coluna ou índice. Foi
  escolha deliberada: é o que garante "nenhuma parada de produção".
- **Contrato HTTP da API.** Nenhuma rota, payload ou código de status muda. O frontend
  não precisa ser alterado junto.
- **Refatoração dos componentes React** (`CortesAutomaticos.jsx`, 2.387 linhas) — merece
  projeto próprio, ver seção 12.
- **Troca de dependências, versão de Django/Celery, ou infraestrutura.**
- **Prompts de LLM em si** (o *conteúdo*). R-18 move os prompts de lugar; não reescreve
  nenhum texto de prompt.

---

## 3. Linha de base

Tudo abaixo foi medido em `3c7f0e8`, não estimado.

| Métrica | Hoje |
| --- | --- |
| **Suíte de testes** | 288 testes, **65s**, `FAILED (errors=7, skipped=2)` no ambiente local |
| Causa dos 7 erros | 6 pelo bug real em `_NoOpMetric` (D-06) + 1 por `prometheus_client` ausente no `.venv` local. **Resolvido em R-01** — hoje 294 testes, 60s, `OK`. |
| Comando real (local) | `manage.py test` com `DJANGO_SETTINGS_MODULE=social_automation.settings_test` |
| Comando real (CI) | `python -m pytest -q` (pytest não estava instalado no `.venv` local; sincronizado durante R-01) |
| **Cobertura declarada** | portão de **70%** sobre **12 módulos = 2.562 linhas = 7,5% do código** |
| **Cobertura real** (medida em R-02) | **40,81%** do código de produção — 12.844 statements, 7.061 sem cobertura, testes e migrations excluídos |
| Cobertura dos arquivos críticos | `auto_cuts/tasks.py` **7%** · `api/views.py` **21%** · `grok.py` **30%** · `social/tasks.py` **41%** · `api/serializers.py` **43%** |
| **Violações de ruff** | **0** — `ruff check .` limpo |
| **Type check** | **não existe** — sem mypy/pyright no projeto nem no CI |
| **LOC Python** | 34.064 (sem `.venv`, migrations, `__pycache__`) |
| LOC de teste | ~6.005 (≈ 21% do total) |
| **Arquivos > 500 linhas** | **15** (ver tabela abaixo) |
| **Funções ≥ 50 linhas** | **108** |
| **Funções ≥ 80 linhas** | **48** |
| Maior função | `_run_post_to_platforms` — **1.189 linhas** (`apps/social/tasks.py:2459`) |
| **`except Exception`** | **175** ocorrências fora de testes; **52** seguidas de `pass` puro |
| **`os.getenv`/`os.environ` fora de settings** | **57** ocorrências em **18 arquivos** |
| **Imports dentro de função** (`from apps.…`) | **96** — sinal de dependência circular |
| **Frontend** | 11.790 LOC, `npm test` existe mas **o CI nunca roda** |
| Repositório | 351 arquivos versionados, `.git` = 13 MB, `.gitignore` saudável, nenhum binário grande versionado |

### Arquivos com mais de 500 linhas

| Linhas | Arquivo |
| --- | --- |
| 4.035 | `apps/social/tasks.py` |
| 2.522 | `apps/api/views.py` |
| 2.111 | `apps/auto_cuts/tasks.py` |
| 2.057 | `apps/auto_cuts/services/grok.py` |
| 1.053 | `apps/social/tests/test_upload_post_reconciliation.py` |
| 1.048 | `apps/api/serializers.py` |
| 936 | `apps/api/factory_youtube_dashboard.py` |
| 743 | `apps/jobs/models.py` |
| 728 | `apps/auto_cuts/services/recovery.py` |
| 684 | `apps/jobs/services/ffmpeg.py` |
| 645 | `apps/jobs/services/daily_posting_plan_service.py` |
| 616 | `apps/api/tests/test_factory_youtube_dashboard.py` |
| 586 | `apps/brands/models.py` |
| 542 | `apps/social/services/upload_post_reconciliation.py` |
| 504 | `apps/jobs/services/factory_scheduler.py` |

### Top 10 funções mais longas

| Linhas | Local |
| --- | --- |
| **1.189** | `apps/social/tasks.py:2459` — `_run_post_to_platforms` |
| 652 | `apps/auto_cuts/tasks.py:886` — `analyze_auto_cuts_task` |
| 556 | `apps/auto_cuts/tasks.py:1555` — `finalizar_auto_cut_task` |
| 406 | `apps/social/tasks.py:1393` — `_try_pending_upload_post_reconciliation` |
| 272 | `apps/auto_cuts/services/thumbnail.py:194` — `generate_auto_thumbnail` |
| 251 | `apps/jobs/services/daily_posting_plan_service.py:394` — `get_or_generate_for_day` |
| 244 | `apps/auto_cuts/tasks.py:611` — `_process_ready_cuts_batch_flow` |
| 219 | `apps/social/services/upload_post_reconciliation.py:251` — `reconcile_upload_post_status` |
| 213 | `apps/social/tasks.py:608` — `_replace_ambiguous_short_slot` |
| 212 | `apps/social/tasks.py:2245` — `reconcile_youtube_full_scan_task` |

### Arquivos que mais mudam (12 meses) × commits de `fix`

| Alterações | Em `fix` | Linhas | Arquivo |
| --- | --- | --- | --- |
| **42** | **15** | 4.035 | `apps/social/tasks.py` |
| 38 | 3 | 2.522 | `apps/api/views.py` |
| 32 | 7 | 2.111 | `apps/auto_cuts/tasks.py` |
| 24 | 0 | 353 | `social_automation/settings.py` |
| 24 | 3 | 1.048 | `apps/api/serializers.py` |
| 18 | 2 | 586 | `apps/brands/models.py` |
| 16 | 3 | 504 | `apps/jobs/services/factory_scheduler.py` |
| 16 | 0 | 2.057 | `apps/auto_cuts/services/grok.py` |
| 14 | 2 | 743 | `apps/jobs/models.py` |
| 13 | 0 | — | `config/celery.py` |

> **Este cruzamento é o mapa do projeto inteiro.** Alta frequência de mudança + alta
> complexidade + alta densidade de correção = `apps/social/tasks.py`. É por onde se começa,
> e é onde 60% do esforço deste plano está alocado.

### Módulos mais acoplados (imports dentro de função — sinal de ciclo)

| Ocorrências | Arquivo |
| --- | --- |
| 27 | `apps/auto_cuts/tasks.py` |
| 19 | `apps/api/views.py` |
| 18 | `apps/social/tasks.py` |
| 4 | `apps/multiple_creator/tasks.py`, `apps/auto_cuts/services/recovery.py`, `apps/auto_cuts/services/grok.py` |

### Estrutura por app

| App | `services/` | Arquivos de teste |
| --- | --- | --- |
| `api` | **0** | 5 |
| `auto_cuts` | 11 | 6 |
| `brands` | **0** | 2 |
| `common` | **0** | 0 |
| `cuts` | **0** | 2 |
| `jobs` | 12 | 11 |
| `mediahub` | **0** | 2 |
| `multiple_creator` | **0** | 4 |
| `social` | 9 | 10 |

---

## 4. Diagnóstico

Ordenado por dor real (frequência de mudança × risco × densidade de correção), não por
gosto estético.

---

### D-01 · `apps/social/tasks.py` é um god module de 4.035 linhas

**Onde:** o arquivo inteiro; o núcleo é `_run_post_to_platforms` (`apps/social/tasks.py:2459`–`3648`),
**1.189 linhas em uma função**.

**O que é.** O arquivo concentra: resolução de marca e conta, verificação de existência
de vídeo no YouTube, cálculo de janela de slot da factory, substituição de slot ambíguo,
idempotência, reconciliação de Upload-Post, publicação nativa no YouTube, fallback entre
publicadores, limpeza de mídia local, primeiro comentário, métricas e logging estruturado.
São 5 tasks Celery e ~50 funções auxiliares privadas no mesmo módulo.

**Por que atrapalha na prática.**
- É o arquivo nº 1 em alteração (42×) **e** nº 1 em commits de `fix` (15×) — mais que o
  dobro do segundo. Toda mudança de publicação passa por aqui.
- Uma função de 1.189 linhas não cabe na cabeça de ninguém. Não dá para revisar um diff
  nela com confiança, e o teste que existe cobre caminhos, não a função.
- Qualquer PR que toque publicação conflita com qualquer outro PR que toque publicação.
  Trabalho paralelo no fluxo mais crítico do produto é praticamente impossível.

**Se nada for feito:** a taxa de retrabalho continua. O arquivo cresce ~340 linhas por
trimestre no ritmo atual, e a próxima plataforma de publicação o empurra para 5.000 linhas.

---

### D-02 · ~~A máquina de estados de publicação está duplicada em 5 lugares, sem dono~~ — RESOLVIDO em R-06 + R-07

> **Fechado em 2026-08-13.** `apps/social/services/posting_state.py` é o dono único: os 5
> sites abaixo passaram a chamá-lo, as 5 divergências foram decididas (L-7) e um teste no
> CI — `test_posting_state_is_the_only_writer_of_posted_status` — quebra o build se uma
> sexta cópia nascer. O diagnóstico fica registrado abaixo como estava, porque é ele que
> explica por que `apps/social/tasks.py` lidera o ranking de `fix`.

**Onde:**

| Local | O que faz |
| --- | --- |
| `apps/social/tasks.py:422` | `item.status = "POSTED"` |
| `apps/social/tasks.py:456` | `item.status = "POSTED"` |
| `apps/social/tasks.py:843` | `item.status = "POSTED"` (dentro de `_mark_factory_posting_verified`) |
| `apps/api/views.py:1502` | `inventory.status = "POSTED"` — **dentro de um handler HTTP** |
| `apps/social/management/commands/fix_youtube_posted_status.py:50` | `item.status = "POSTED"` |

**O que é.** A transição "este vídeo foi publicado" envolve coordenar 4 modelos —
`ScheduledPost`, `FactoryPostingSchedule`, `VideoInventoryItem`, `PostedVideoLog`. Não
existe uma função que seja **a** dona dessa transição. Existem cinco variações, escritas
em momentos diferentes, com conjuntos diferentes de `update_fields`.

> **Medido em R-03 (2026-08-11).** As cinco cópias concordam em **apenas 3 campos**:
> `schedule.status=DONE`, `item.status=POSTED`, `item.last_error=""`. Divergências reais,
> cada uma travada por um teste:
>
> | # | Divergência | Quem faz diferente |
> | --- | --- | --- |
> | 1 | **Não deduplica `PostedVideoLog` e não valida id vazio** → gera log duplicado e log com `external_video_id=""` | só **B** |
> | 2 | Não zera `schedule.next_retry_at` | só **D** |
> | 3 | Não atualiza `attempt_count` | **D** e **E** |
> | 4 | Preserva `posted_at`/`scheduled_for` em vez de sobrescrever | só **E** |
> | 5 | Dirige o próprio `ScheduledPost` para `DONE` | só **C** |
>
> Legenda: A=`tasks.py:414` (YouTube), B=`tasks.py:452` (demais), C=`tasks.py:823`,
> D=`views.py:1447`, E=`fix_youtube_posted_status.py:47`.
>
> **A divergência 1 é bug, não intenção** — não há motivo para o ramo não-YouTube gerar
> log duplicado. As outras quatro precisam de decisão (ver L-7).

> **Bug encontrado ao caracterizar (R-21, corrigido em [#30](https://github.com/Cascapera/social_automation/pull/30)):**
> a cópia **C** levantava `ValueError` em **toda** chamada, porque incluía `updated_at`
> num `update_fields` de `ScheduledPost` — modelo que não tem esse campo. Como as duas
> tasks de reconciliação do YouTube a chamam de dentro de um `except Exception` amplo, o
> erro se disfarçava de "reconciliação falhou" e abortava a rodada inteira no primeiro
> vídeo confirmado. **Nenhum item chegava a `POSTED` por esse caminho** — o que é
> consistente com a existência do management command de reparo manual (cópia E).

**Por que atrapalha na prática.** É a causa direta do ranking de `fix`. Quando um estado
fica inconsistente, a correção é aplicada no caminho que o bug apareceu — e os outros
quatro continuam com o comportamento antigo. A existência do management command
`fix_youtube_posted_status.py` é a prova documental: alguém precisou escrever um script
para consertar à mão estados que o código deixou errados.

**Se nada for feito:** todo novo canal ou plataforma de publicação vira uma sexta cópia.

---

### D-03 · Transições de múltiplos modelos sem transação

**Onde:** `apps/social/tasks.py:823`–`867` (`_mark_factory_posting_verified`).

```
post.save(update_fields=[...])       # linha 838  → ScheduledPost = DONE
schedule.save(update_fields=[...])   # linha 842  → FactoryPostingSchedule = DONE
item.save(update_fields=[...])       # linha 848  → VideoInventoryItem = POSTED
PostedVideoLog.objects.create(...)   # linha 854  → log de auditoria
```

Quatro escritas, quatro modelos, **nenhum `transaction.atomic()`**.

**Números:** o arquivo tem **43** chamadas a `save(update_fields=)` e **1** único
`transaction.atomic`. `_mark_factory_posting_still_scheduled` (`apps/social/tasks.py:870`)
tem o mesmo problema com 2 modelos.

**Por que atrapalha na prática.** Se o worker Celery morrer, o Postgres cair ou a task for
revogada entre as linhas 838 e 848 — coisa que acontece: `acks_late=True` está ligado e a
task é reexecutada — o sistema fica com `ScheduledPost=DONE` e `VideoInventoryItem=SCHEDULED`.
O vídeo já está no ar e o inventário acha que ainda precisa postar. Esse é exatamente o
tipo de estado que o `fix_youtube_posted_status.py` existe para limpar.

**Se nada for feito:** inconsistências silenciosas continuam, detectadas só quando um vídeo
é publicado duas vezes ou some da lista.

---

### D-04 · Regra de negócio dentro de handler HTTP; `apps.api` não tem camada de serviço

**Onde:** `apps/api/views.py` — 2.522 linhas, 26 classes, **`services/` = 0 arquivos**.

Exemplos concretos:
- `VideoInventoryItemViewSet.remove_awaiting` (`apps/api/views.py:1189`–`1248`): abre
  `transaction.atomic()`, apaga arquivos de mídia do disco, apaga `ScheduledPost`,
  `FactoryPostingSchedule` e `VideoInventoryItem`. Tudo dentro do método da view.
- `VideoInventoryItemViewSet.retry_posting` (`apps/api/views.py:1250`–`1376`, **126 linhas**):
  reativa o ciclo de postagem coordenando 3 modelos e enfileirando task.
- `apps/api/views.py:1607` — `create` com 137 linhas.

**Por que atrapalha na prática.**
- Essa regra só é testável através de um request HTTP autenticado. Não dá para chamá-la
  do shell, de um management command ou de uma task sem duplicar.
- E ela **foi** duplicada: `apps/api/views.py:1502` é a quinta cópia do D-02.
- 19 imports de `apps.*` dentro de funções neste arquivo — a view precisa de tudo, e para
  não criar ciclo o import foi empurrado para dentro do método.

---

### D-05 · O portão de cobertura mede 7,5% do código — e não mede o que dói

**Onde:** `pyproject.toml:14`–`27`.

O `addopts` lista 12 módulos em `--cov=` e exige `--cov-fail-under=70`. Somados, esses
módulos têm **2.562 linhas de 34.064** — e a maior parte é declarativa (`models.py`,
`urls.py`, `settings.py`).

**Não estão medidos:** `apps/social/tasks.py` (4.035), `apps/api/views.py` (2.522),
`apps/auto_cuts/tasks.py` (2.111), `apps/auto_cuts/services/grok.py` (2.057) — ou seja,
os quatro arquivos que concentram as correções.

**Por que atrapalha na prática.** O número "70% de cobertura" no CI dá uma sensação de
segurança que não corresponde ao risco real. Pior: **refatorar sem rede é o que este plano
proíbe na seção 6**, e hoje não há como saber onde a rede existe. Sem corrigir isto
primeiro, o resto do plano não pode começar com segurança.

---

### D-06 · `_NoOpMetric` não implementa `observe()` — falha no cenário para o qual foi escrito

**Onde:** `apps/common/metrics.py:21`–`35` × uso em `apps/social/tasks.py:3599`.

```python
except ImportError:  # e.g. image not rebuilt after requirements.txt change   ← metrics.py:12
    class _NoOpChild:
        def inc(...)      # ok
        def observe(...)  # ok
    class _NoOpMetric:
        def labels(...) -> _NoOpChild
        def inc(...)      # ok
        # observe() NÃO EXISTE                                                 ← metrics.py:21-29
```

`_NoOpChild` (usado via `.labels(...)`) tem `observe`. `_NoOpMetric` (usado direto, sem
labels) **não tem**. E `publish_duration_ms.observe(...)` em `apps/social/tasks.py:3599` é
chamado sem labels.

**Por que atrapalha na prática.** O fallback existe justamente para o caso "imagem não
reconstruída depois de mudar `requirements.txt`". Nesse cenário, ao invés de degradar
silenciosamente, **a publicação quebra** com `AttributeError: '_NoOpMetric' object has no
attribute 'observe'`. É o modo de falha que o código tentou evitar, acontecendo por causa
do próprio remendo. Foi o que derrubou 7 testes na execução local desta análise.

> ⚠️ **Isto é uma correção de bug, não refatoração.** Está no plano como R-01 porque
> destrava a suíte local, mas vai em **PR próprio**, marcado como `fix`, sem misturar
> com nenhum movimento estrutural.

---

### D-07 · 96 imports dentro de função — dependência circular não resolvida

**Onde:** concentrados em `apps/auto_cuts/tasks.py` (27), `apps/api/views.py` (19),
`apps/social/tasks.py` (18).

**O que é.** `from apps.x import y` escrito indentado, dentro do corpo da função, para
adiar a resolução do import e quebrar um ciclo em tempo de execução.

**Por que atrapalha na prática.** É o sintoma, não a doença: significa que views e tasks
importam umas às outras, direta ou indiretamente. O custo é que a estrutura real de
dependências fica invisível — nenhuma ferramenta de análise estática enxerga um import
que só existe em runtime — e o erro de ciclo aparece só quando aquele caminho executa,
possivelmente em produção.

---

### D-08 · Configuração espalhada: 57 `os.getenv` fora de `settings.py`

**Onde:** 18 arquivos, incluindo `apps/social/tasks.py:65`–`68`:

```python
YOUTUBE_CHECK_CLIENT_ENABLED = bool(
    (os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()
    and (os.getenv("YOUTUBE_CHECK_CLIENT_SECRET") or "").strip()
)
```

**Por que atrapalha na prática.** Isso é lido **no momento do import do módulo**. Um teste
não consegue alterar esse comportamento com `override_settings` nem com `patch.dict(os.environ)`
— o valor já foi congelado. Na prática, o caminho "YouTube check habilitado" é intestável.
O `settings.py` faz a coisa certa (centraliza 30+ variáveis); o problema é que existe uma
segunda fonte da verdade fora dele.

---

### D-09 · ~~`grok.py`: ~1.235 linhas de prompt misturadas com cliente HTTP~~ — RESOLVIDO em R-18

> **Fechado em 2026-08-13** ([#38](https://github.com/Cascapera/social_automation/pull/38)).
> `grok.py` foi de **2.057 para 897 linhas**; prompts, vocabulário e tabela de preço estão
> em `apps/auto_cuts/prompts/` e `grok_pricing.py`. O `git blame` de prompt voltou a
> funcionar, e o teste de hash faz a mudança editorial **aparecer na revisão** em vez de se
> esconder num diff de código — que era a segunda metade da queixa deste diagnóstico.



**Onde:** `apps/auto_cuts/services/grok.py` — 2.057 linhas. A primeira função só aparece
na **linha 1236**. Antes disso: tabela de preços (`GROK_PRICING`, linha 39), listas de
palavras de CTR e palavras proibidas em PT e EN (linhas 78–137), e ~10 blocos de prompt
de sistema e template (linhas 150–1235).

Depois da linha 1236 convivem no mesmo arquivo: parsing de JSON, coerção de tipos,
cálculo de custo em USD, emissão de métricas, construção do cliente LLM e as 4 funções
públicas de análise.

**Por que atrapalha na prática.** Ajustar uma palavra de um prompt exige mexer no mesmo
arquivo que contém o cliente HTTP e o cálculo de custo — o `git blame` fica inútil e o
diff de "mudei o prompt" tem o mesmo peso de revisão que "mudei o cliente". São conteúdo
e código com ciclos de vida completamente diferentes no mesmo lugar.

---

### D-10 · Tratamento de erro ad-hoc: 175 `except Exception`, 52 seguidos de `pass`

**Onde:** exemplo em `apps/api/views.py:1219`–`1229`:

```python
try:
    corte.file.delete(save=False)
    deleted_files += 1
except Exception:
    pass
```

**Por que atrapalha na prática.** Apagar arquivo do disco falhou? Ninguém fica sabendo. O
contador `deleted_files` mente para o cliente da API, o arquivo vira órfão no
`MEDIA_ROOT`, e não há log nem métrica. O projeto tem `log_event` estruturado
(`apps/jobs/logging_utils.py`) usado 40 vezes só em `social/tasks.py` — a infraestrutura
existe, mas 52 pontos a ignoram.

---

### D-11 · `apps/auto_cuts/tasks.py`: duas funções somam 1.208 linhas

**Onde:** `analyze_auto_cuts_task` (`apps/auto_cuts/tasks.py:886`, **652 linhas**) e
`finalizar_auto_cut_task` (`apps/auto_cuts/tasks.py:1555`, **556 linhas**).

Mesma doença de D-01, um grau mais leve: 32 alterações, 7 em `fix`. O app **tem**
`services/` com 11 módulos — a camada existe, mas as tasks não a usam de forma
consistente (27 imports dentro de função é o indicador).

---

### D-12 · Frontend: testes existem e o CI não roda

**Onde:** `frontend/package.json` define
`"test": "node --test src/utils/youtubeSummaryCache.test.js src/utils/factoryWeeklySchedule.test.js"`.
`.github/workflows/ci.yml:52`–`57` roda **só** `npm ci && npm run build`.

**Por que atrapalha na prática.** Dois arquivos de teste foram escritos e nunca são
executados por ninguém além de quem lembra de rodar à mão. Podem estar quebrados agora
mesmo. Corrigir é uma linha de YAML.

---

## 5. Estado-alvo

### Princípio

Não há mudança de paradigma proposta. A arquitetura em camadas que `docs/ARCHITECTURE.md`
já descreve está **certa** — ela só não é obedecida em 4 arquivos. O alvo é fazer o código
igualar a documentação, não inventar uma arquitetura nova.

Padrão: **camadas com regra de dependência explícita**. Não hexagonal, não ports & adapters —
seria indireção cara demais para um projeto deste porte, com um único consumidor
(o próprio frontend) e um único banco. O que falta não é abstração; é **fronteira**.

### Regra de dependência (passa a valer)

```
        models/          ← ninguém abaixo importa acima
           ↑
        services/        ← pode importar models, nunca tasks nem views
           ↑
   ┌───────┴───────┐
 tasks/          views/  ← orquestram, nunca contêm regra
```

- `services/` **nunca** importa `tasks` nem `views`.
- `tasks` e `views` **não** importam um ao outro.
- Todo `from apps.…` fica no topo do arquivo. Import dentro de função vira sinal de
  violação da regra, não solução.

### Antes → depois (`apps/social`)

```
ANTES                                    DEPOIS
apps/social/                             apps/social/
├── tasks.py            4.035 linhas     ├── tasks.py                    ~250 linhas
│   ├── 5 tasks Celery                   │   └── só as 5 tasks Celery, finas,
│   ├── ~50 funções auxiliares           │       delegando para services/
│   ├── máquina de estados (3 cópias)    ├── services/
│   ├── resolução de destino             │   ├── posting_state.py      ← R-06 dono único
│   ├── publicação YouTube nativa        │   │     mark_posted() / mark_failed() /
│   ├── publicação Upload-Post           │   │     mark_still_scheduled()   [atomic]
│   └── finalização e limpeza            │   ├── publish_targets.py     ← R-08
├── services/  (9 módulos)               │   ├── publishing/
├── publishers/                          │   │   ├── preflight.py       ← R-09
└── tests/                               │   │   ├── youtube_native.py  ← R-10
                                         │   │   ├── upload_post.py     ← R-11
apps/api/                                │   │   └── finalize.py        ← R-12
├── views.py            2.522 linhas     │   └── (9 módulos atuais)
├── serializers.py      1.048 linhas     ├── publishers/
└── (sem services/)                      └── tests/

                                         apps/api/
                                         ├── views/                     ← R-15
                                         │   ├── __init__.py  (reexporta tudo)
                                         │   ├── brands.py
                                         │   ├── inventory.py
                                         │   ├── auto_cuts.py
                                         │   ├── jobs.py
                                         │   └── dashboard.py
                                         ├── serializers/               ← R-16
                                         └── (regra de negócio movida para
                                              apps/jobs/services/inventory_actions.py — R-14)

apps/auto_cuts/                          apps/auto_cuts/
├── tasks.py            2.111 linhas     ├── tasks.py                    ~300 linhas
└── services/grok.py    2.057 linhas     ├── prompts/                   ← R-18
    ├── ~1.235 de prompts                │   ├── __init__.py
    └── cliente + parsing + custo        │   ├── system.py
                                         │   ├── templates.py
                                         │   └── vocabulary.py
                                         └── services/
                                             ├── grok.py       ~600 linhas (só cliente)
                                             ├── grok_pricing.py
                                             └── analysis_flow.py       ← R-19
```

### Por que esta forma

- **`posting_state.py` é o coração da proposta.** Um módulo, três funções, cada uma
  atômica, uma única definição de "o que significa publicado". As 5 cópias do D-02 passam
  a chamar a mesma função. É a mudança que ataca diretamente o arquivo campeão de `fix`.
- **`publishing/` como pacote de passos** transforma uma função de 1.189 linhas em 4
  módulos com fronteira clara, cada um testável isoladamente sem subir uma `ScheduledPost`
  completa.
- **`views/` como pacote** é movimentação mecânica: mesmo conteúdo, arquivos menores,
  `__init__.py` reexportando para `urls.py` não mudar. Diff de mover, sem lógica junto.
- **`prompts/` separado** dá a prompts o ciclo de vida que eles têm: são conteúdo, mudam
  por motivo editorial, e não deveriam compartilhar arquivo com um cliente HTTP.

---

## 6. Rede de segurança

**Passo zero e não negociável.** Hoje, os 4 arquivos que este plano quer mexer estão
**fora** do portão de cobertura (D-05). Refatorar assim é apostar.

### O que existe

| Área | Estado |
| --- | --- |
| `apps/social/tests/` | 1.917 LOC, 10 arquivos. `test_upload_post_reconciliation.py` (1.053 linhas) e `test_publish_idempotency.py` cobrem partes do fluxo de publicação — mas por caminho, não por transição de estado. |
| `apps/jobs/tests/` | 1.387 LOC, 11 arquivos — a área mais bem coberta |
| `apps/api/tests/` | 1.205 LOC, 5 arquivos |
| `apps/auto_cuts/tests/` | 827 LOC, 6 arquivos |
| `apps/brands`, `cuts`, `mediahub` | 18, 40 e 27 LOC — praticamente sem teste |

### Characterization tests a escrever antes de qualquer refatoração

| # | Área | O que fixar | Esforço |
| --- | --- | --- | --- |
| **CT-1** | Máquina de estados de publicação | Para cada uma das 5 cópias do D-02: o estado exato dos 4 modelos **antes e depois**, incluindo `update_fields` e o `PostedVideoLog` criado. Inclusive o comportamento esquisito (ex.: `_mark_factory_posting_verified` retornar silenciosamente quando não há `schedule` — `apps/social/tasks.py:831`). | 6h → **R-03** |
| **CT-2** | `_run_post_to_platforms` | Caminho feliz + as ~~6~~ **9** guardas de saída antecipada (`tasks.py` 2479, 2484, 2497, 2503, 2512, 2517, 2523, 2556, 2596) + slot expirado + factory pausada. ✅ feito no R-04 — 22 testes. A contagem original omitia as duas guardas do ramo de corte e a de post sem origem. | 1d → **R-04** |
| **CT-3** | Ações de inventário via API | `remove_awaiting` e `retry_posting` (`apps/api/views.py:1189` e `:1250`) — resposta HTTP e efeito no banco. | 4h → dentro de **R-14** |
| **CT-4** | Fluxo `auto_cuts` | `analyze_auto_cuts_task` e `finalizar_auto_cut_task` — só as fronteiras (entrada, estado final da análise, tasks enfileiradas). | 1d → dentro de **R-19** |

### Áreas intestáveis no design atual

- ~~**`YOUTUBE_CHECK_CLIENT_ENABLED` (`apps/social/tasks.py:65`)** — lido no import.~~
  **A premissa estava errada, e o R-17 mostrou por quê (2026-08-13).** Não havia ramo
  intestável: a constante era **código morto** — nada no repositório a lia. Foi removida.
  A configuração `YOUTUBE_CHECK_*` está em `settings.py` desde o R-17 lote 1
  ([#37](https://github.com/Cascapera/social_automation/pull/37)), e o ramo de verdade que
  a migração destravou — `get_check_client_config()` — tem teste. **Não existe, portanto,
  dependência técnica atravessando ondas.**
- **Regra dentro de view (`apps/api/views.py:1189`, `:1250`)** — só testável via request
  HTTP. R-14 resolve extraindo para serviço; até lá, CT-3 testa por HTTP mesmo.

### Métrica que habilita tudo

**R-02** troca o portão de vaidade por um portão real: `--cov=apps --cov=social_automation`
com piso definido pela medição do dia (não 70% inventado), e **catraca** — o piso só sobe.
Sem esse número, não há como afirmar "a refatoração não perdeu cobertura".

---

## 7. Backlog de refatorações

18 itens. Todos com PR alvo de ≤ ~400 linhas. **Nenhum exige parada de produção.**

---

```
[R-01] Corrigir _NoOpMetric.observe() ausente
Motivação:   D-06 — fallback de métricas quebra publicação quando prometheus_client falta
Arquivos:    apps/common/metrics.py, apps/common/tests/test_metrics_fallback.py (novo)
O que muda:  adicionar observe(value) a _NoOpMetric; teste que simula ImportError e
             chama .inc(), .observe() e .labels(...).observe()
Não muda:    nada quando prometheus_client está presente — o caminho normal é idêntico
Pré-requisito: nenhum — é o primeiro item
PR:          ~40 linhas · 2 arquivos
Produção:    transparente
Deploy:      deploy normal. Sem flag, sem migração, sem ordem.
Como validar: manage.py test apps.common; e a suíte completa passa de 7 erros para 0
             no ambiente sem prometheus_client
Verificação pós-deploy: métrica publish_duration_ms continua aparecendo em /metrics
Risco:       baixo
Reversão:    rollback simples
Esforço:     30 min
Ganho:       suíte local verde — pré-condição para todo o resto do plano
⚠ NÃO É REFATORAÇÃO: é correção de bug. PR próprio, prefixo fix(), sem misturar.
```

```
[R-21] Corrigir update_fields com campo inexistente em ScheduledPost
Motivação:   D-02 — descoberto ao escrever R-03. ScheduledPost não tem updated_at, mas
             dois pontos o incluíam em update_fields, levantando ValueError SEMPRE
Arquivos:    apps/social/tasks.py:838 e :3837, apps/social/tests/test_scheduled_post_save_fields.py
O que muda:  remover "updated_at" dos dois update_fields
Não muda:    nenhum schema. O modelo realmente não tem o campo; adicioná-lo seria
             mudança de banco, fora do escopo deste projeto
Pré-requisito: nenhum — bloqueia R-03, então vem antes
PR:          ~157 linhas · 2 arquivos
Produção:    transparente no deploy, mas MUDA COMPORTAMENTO (ver abaixo)
Deploy:      deploy normal
Como validar: 3 dos 4 testes novos falham sem a correção (verificado com git stash)
Verificação pós-deploy: publish_reconciliation_failures_total deve CAIR; itens devem
             começar a sair de "Aguardando Postagem" sozinhos
Risco:       baixo na correção, médio no efeito — ver abaixo
Reversão:    rollback simples
Esforço:     2h (feito)
Ganho:       a reconciliação do YouTube volta a funcionar; a idempotência do primeiro
             comentário passa a valer
⚠ NÃO É REFATORAÇÃO: é correção de bug, PR próprio com prefixo fix().
⚠ MUDA COMPORTAMENTO OBSERVÁVEL: hoje a reconciliação aborta na primeira confirmação.
   Depois do deploy ela vai processar a fila acumulada e marcar itens como POSTED em
   lote. Avisar quem acompanha os painéis — o pico é o conserto, não um incidente.
```

```
[R-02] Portão de cobertura sobre o código real, com catraca
Motivação:   D-05 — hoje mede 7,5% do código e ignora os 4 arquivos que mais quebram
Arquivos:    pyproject.toml, requirements-dev.txt, docs/ (nota curta)
O que muda:  --cov=apps --cov=social_automation no lugar da lista de 12 módulos;
             --cov-fail-under passa a ser o valor MEDIDO no dia (provavelmente bem
             abaixo de 70) e vira catraca: sobe, nunca desce
Não muda:    nenhum código de produção; nenhum teste é alterado
Pré-requisito: R-01 (suíte precisa estar verde para a medição valer)
PR:          ~25 linhas · 2 arquivos
Produção:    transparente (não vai para produção — só CI)
Deploy:      n/a
Como validar: python -m pytest -q no CI; o relatório term-missing passa a listar
             social/tasks.py, api/views.py, auto_cuts/tasks.py, grok.py
Verificação pós-deploy: n/a
Risco:       baixo — o único risco é o CI ficar vermelho ao revelar o número real;
             por isso o piso é definido PELA medição, não por meta
Reversão:    rollback simples
Esforço:     2h
Ganho:       a partir daqui é possível provar que uma refatoração não perdeu cobertura.
             Habilita literalmente todo o resto do plano.
```

```
[R-03] Characterization tests da máquina de estados de publicação (CT-1)
Motivação:   D-02/D-03 — antes de unificar 5 cópias, fixar o que cada uma faz hoje
Arquivos:    apps/social/tests/test_posting_state_characterization.py (novo)
O que muda:  nada de produção. Testes que capturam o estado dos 4 modelos antes/depois
             para cada uma das 5 cópias, incluindo comportamentos esquisitos
             (retorno silencioso sem schedule — apps/social/tasks.py:831)
Não muda:    nenhuma linha de código de produção
Pré-requisito: R-02
PR:          ~300 linhas · 1 arquivo
Produção:    transparente (só teste)
Deploy:      n/a
Como validar: os testes passam CONTRA O CÓDIGO ATUAL, sem alterá-lo. Se algum não
             passar, o comportamento real é outro — corrigir o teste, não o código.
Verificação pós-deploy: n/a
Risco:       baixo
Reversão:    rollback simples
Esforço:     6h
Ganho:       a rede que torna R-06 e R-07 seguros
```

```
[R-04] Characterization tests de _run_post_to_platforms (CT-2)
Motivação:   D-01 — não se fatia uma função de 1.189 linhas sem rede
Arquivos:    apps/social/tests/test_run_post_to_platforms_characterization.py (novo)
O que muda:  nada de produção. Cobre caminho feliz + 6 guardas de saída antecipada
             (apps/social/tasks.py:2476, 2481, 2494, 2500, 2520, 2553) + slot expirado
             + factory pausada
Não muda:    nenhuma linha de produção
Pré-requisito: R-02
PR:          ~380 linhas · 1 arquivo  (se passar disso, quebrar em 2 PRs por grupo de caminho)
Produção:    transparente (só teste)
Deploy:      n/a
Como validar: passam contra o código atual sem alterá-lo
Verificação pós-deploy: n/a
Risco:       baixo
Reversão:    rollback simples
Esforço:     1 dia
Ganho:       destrava R-09 a R-13 — a parte mais pesada do plano
LIMITE CONHECIDO — ENCERRADO no R-17 (2026-08-13): o "ramo de
             YOUTUBE_CHECK_CLIENT_ENABLED" não existia. A constante era código morto,
             lida por ninguém. Não havia o que cobrir; foi removida.
CONCLUÍDO em 2026-08-12: 22 testes, 9 guardas (não 6). Descobriu R-22 e R-23.
```

```
[R-22] Corrigir guarda inalcançável de "Job sem vídeo final"
Motivação:   descoberto ao escrever R-04. apps/social/tasks.py:2498 faz
             `output = post.job.output` — acesso a um OneToOne reverso. Quando o job não
             tem nenhum RenderOutput, o ACESSO levanta RelatedObjectDoesNotExist, e a
             guarda da linha 2503, que trataria o caso como FAILED, nunca executa
Arquivos:    apps/social/tasks.py:2498,
             apps/social/tests/test_run_post_to_platforms_characterization.py
O que muda:  trocar o acesso direto por getattr(post.job, "output", None) (ou try/except
             RenderOutput.DoesNotExist); inverter o teste
             test_job_sem_render_output_levanta_excecao_em_vez_de_falhar, que hoje afirma
             o comportamento errado
Não muda:    o caminho em que o RenderOutput existe mas está vazio — esse já funciona e
             tem teste próprio (test_job_com_render_output_vazio_marca_failed)
Pré-requisito: R-04 (feito) — o teste que caracteriza o bug já está no repo
PR:          ~20 linhas · 2 arquivos
Produção:    MUDA COMPORTAMENTO — ver abaixo
Deploy:      deploy normal. Sem flag, sem migração.
Como validar: o teste caracterizador inverte de assertRaises para assertEqual FAILED;
             falha antes da correção, passa depois
Verificação pós-deploy: posts de job sem render parado em PENDING devem passar a virar
             FAILED com motivo legível, em vez de estourar a task
Risco:       baixo
Reversão:    rollback simples
Esforço:     30 min
Ganho:       o post deixa de ficar presos em PENDING e o motivo aparece no painel em vez
             de só no log do worker
⚠ NÃO É REFATORAÇÃO: é correção de bug. PR próprio, prefixo fix().
⚠ MUDA COMPORTAMENTO OBSERVÁVEL: hoje a task estoura e queima as 3 tentativas de retry
   sem registrar nada no post. Depois da correção o post vira FAILED na primeira
   tentativa. Itens que hoje parecem "travados em PENDING" vão aparecer como FAILED —
   é o diagnóstico ficando visível, não uma regressão.
```

```
[R-23] Limpar post.error ao concluir uma publicação com sucesso
Motivação:   descoberto ao escrever R-04. No ramo de sucesso (apps/social/tasks.py:3565)
             post.error só é escrito quando há warnings. Sem warnings, o texto da
             tentativa anterior sobrevive — e "error" está no update_fields, então é
             gravado de volta junto com status=DONE
Arquivos:    apps/social/tasks.py:3565,
             apps/social/tests/test_run_post_to_platforms_characterization.py
O que muda:  no ramo de sucesso, zerar post.error quando não houver warnings; inverter a
             asserção do teste test_publicacao_bem_sucedida_deixa_o_post_em_done
Não muda:    o ramo com warnings, que deve continuar preservando o texto; nem o ramo de
             falha
Pré-requisito: R-04 (feito)
PR:          ~10 linhas · 2 arquivos
Produção:    transparente no deploy; corrige exibição
Deploy:      deploy normal
Como validar: o teste caracterizador inverte de "erro anterior" para ""
Verificação pós-deploy: posts DONE que hoje mostram erro no painel param de mostrar
Risco:       baixo — só afeta posts que já estão em DONE
Reversão:    rollback simples
Esforço:     20 min
Ganho:       o painel para de mostrar erro em publicação que deu certo; o retry deixa de
             deixar rastro falso
⚠ NÃO É REFATORAÇÃO: é correção de bug. PR próprio, prefixo fix().
⚠ Só afeta posts que já passaram por pelo menos uma tentativa falha antes do sucesso.
```

```
[R-05] Rodar os testes do frontend no CI
Motivação:   D-12 — dois arquivos de teste existem e ninguém executa
Arquivos:    .github/workflows/ci.yml
O que muda:  adicionar `npm test` ao job frontend, depois do build
Não muda:    nenhum código de aplicação
Pré-requisito: nenhum — roda em paralelo com tudo
PR:          ~3 linhas · 1 arquivo
Produção:    transparente (só CI)
Deploy:      n/a
Como validar: o job frontend do CI executa e reporta os 2 arquivos de teste
Verificação pós-deploy: n/a
Risco:       baixo — pode revelar que um dos testes já está quebrado. Se estiver,
             corrigir vai em PR SEPARADO, com prefixo fix().
Reversão:    rollback simples
Esforço:     1h
Ganho:       o pouco de teste que o frontend tem passa a valer alguma coisa
```

```
[R-06] Criar apps/social/services/posting_state.py — dono único das transições
Motivação:   D-02 e D-03 — 5 cópias sem dono, 4 modelos sem transação
Arquivos:    apps/social/services/posting_state.py (novo), apps/social/tasks.py
O que muda:  mover _mark_factory_posting_verified (tasks.py:823-867) e
             _mark_factory_posting_still_scheduled (tasks.py:870-893) para o novo módulo
             como mark_posted() / mark_still_scheduled(); envolver cada uma em
             transaction.atomic(). tasks.py passa a importar do serviço.
Não muda:    o estado final dos 4 modelos é bit a bit o mesmo; mesmos update_fields;
             mesmo PostedVideoLog. A ÚNICA diferença observável é atomicidade.
Pré-requisito: R-03
PR:          ~180 linhas · 3 arquivos
Produção:    REQUER CUIDADO (não parada) — ver seção 9
Deploy:      deploy normal, mas em janela de baixo tráfego de publicação. Motivo: a
             transação passa a segurar lock em 4 tabelas por alguns ms. Nenhuma flag
             necessária; nenhuma migração; convive com workers da versão antiga rodando
             (as duas versões escrevem os mesmos campos).
Como validar: R-03 verde sem alteração + novo teste que aborta no meio da transição e
             prova que nenhum dos 4 modelos foi alterado
Verificação pós-deploy: por 24h, contagem de ScheduledPost DONE cujo VideoInventoryItem
             não está POSTED deve ser 0 (é a inconsistência que isto elimina). Olhar
             também tempo de lock / deadlock no Postgres.
Risco:       médio — a transação é nova; se houver chamada externa lenta dentro do
             bloco, o lock dura mais que o esperado. Mitigação: conferir no diff que
             nenhuma chamada de rede ficou dentro do atomic.
Reversão:    rollback simples — volta ao comportamento não atômico
Esforço:     6h
Ganho:       acaba a classe de bug que gerou fix_youtube_posted_status.py
```

```
[R-07] Apontar as 5 cópias para posting_state
Motivação:   D-02 — enquanto houver cópia, o bug volta
Arquivos:    apps/social/tasks.py (linhas 422, 456), apps/api/views.py (linha 1502),
             apps/social/management/commands/fix_youtube_posted_status.py (linha 50)
O que muda:  cada um dos 4 sites restantes passa a chamar posting_state.mark_posted().
             Onde o comportamento antigo divergir do canônico, a divergência é
             DOCUMENTADA no PR e o teste de R-03 é ajustado explicitamente.
Não muda:    a intenção de cada site. Se algum divergia por bug, a correção é apontada
             no PR — e se for grande, vira item fix() separado.
Pré-requisito: R-06
PR:          ~150 linhas · 4 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: R-03 verde; grep por 'status = "POSTED"' deve retornar 1 site (o serviço)
Verificação pós-deploy: mesma checagem de consistência de R-06 por mais 24h
Risco:       médio — é onde diferenças escondidas entre as cópias aparecem. Por isso
             vem DEPOIS de R-06, com a rede já montada.
Reversão:    rollback simples
Esforço:     4h
Ganho:       uma única definição de "publicado" no sistema inteiro
```

```
[R-08] Extrair resolução de destino para services/publish_targets.py
Motivação:   D-01 — 8 funções de resolução (_resolve_*, _first_youtube_platform,
             _list_ordered_youtube_credentials) ocupam as linhas 114-303 de tasks.py
Arquivos:    apps/social/services/publish_targets.py (novo), apps/social/tasks.py
O que muda:  mover apps/social/tasks.py:114-303 para o novo módulo. Movimento
             puramente mecânico — nenhuma linha de corpo de função é editada.
Não muda:    nada. É recorte e colagem com ajuste de import.
Pré-requisito: R-04
PR:          ~200 linhas movidas · 2 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: suíte completa verde; diff revisado como "movimentação pura"
Verificação pós-deploy: nenhuma específica
Risco:       baixo
Reversão:    rollback simples
Esforço:     3h
Ganho:       -190 linhas em tasks.py; funções de resolução testáveis sem Celery
```

```
[R-09] Fatiar _run_post_to_platforms (1/4): preflight
Motivação:   D-01 — a função de 1.189 linhas
Arquivos:    apps/social/services/publishing/preflight.py (novo), apps/social/tasks.py
O que muda:  extrair apps/social/tasks.py:2466-2555 — carga do post, validação de
             origem (job vs corte), resolução de brand, slot expirado, reconciliação
             antecipada, claim atômico do status PENDING→POSTING — para uma função
             preflight(post_id) -> PreflightResult | EarlyExit
             Técnica: extrair função + introduzir objeto-resultado.
Não muda:    cada saída antecipada devolve exatamente o mesmo dict de hoje
Pré-requisito: R-04, R-08
PR:          ~250 linhas · 3 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: R-04 verde sem nenhuma alteração nos testes
Verificação pós-deploy: taxa de publish_attempts_total e publish_failures_total estável
Risco:       médio — é a primeira fatia da função crítica
Reversão:    rollback simples
Esforço:     6h
Ganho:       -90 linhas na função; as guardas de entrada ficam testáveis isoladamente
```

```
[R-10] Fatiar _run_post_to_platforms (2/4): publicação nativa YouTube
Motivação:   D-01
Arquivos:    apps/social/services/publishing/youtube_native.py (novo), apps/social/tasks.py
O que muda:  extrair o ramo de publicação nativa no YouTube para uma função com
             assinatura explícita (post, brand, video_path, correlation_id) -> StepResult
Não muda:    ordem das chamadas, payload enviado ao YouTube, external_ids gravados,
             eventos de log e métricas emitidas
Pré-requisito: R-09
PR:          ~350 linhas · 3 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: R-04 verde; testes existentes de publishers/youtube.py verdes
Verificação pós-deploy: taxa de sucesso de publicação no YouTube nas primeiras 24h
Risco:       médio-alto — caminho crítico de receita do produto
Reversão:    rollback simples
Esforço:     8h
Ganho:       o ramo mais complexo isolado e testável sem passar pela função inteira
```

```
[R-11] Fatiar _run_post_to_platforms (3/4): ramo Upload-Post
Motivação:   D-01
Arquivos:    apps/social/services/publishing/upload_post.py (novo), apps/social/tasks.py
O que muda:  extrair o ramo Upload-Post, incluindo idempotência (tasks.py:1117-1191)
             e o tratamento de resultado UNKNOWN
Não muda:    chave de idempotência gerada, plataformas enviadas, política de retry
Pré-requisito: R-09
PR:          ~350 linhas · 3 arquivos
Produção:    transparente
Deploy:      deploy normal. Pode ir em paralelo com R-10 (ramos independentes), mas
             preferir sequencial para o diff de tasks.py não conflitar.
Como validar: R-04 verde; test_publish_idempotency.py e
             test_upload_post_reconciliation.py verdes SEM alteração
Verificação pós-deploy: upload_post_unknown_results_total não pode subir
Risco:       médio-alto
Reversão:    rollback simples
Esforço:     8h
Ganho:       idempotência deixa de estar entrelaçada com o resto do fluxo
```

```
[R-12] Fatiar _run_post_to_platforms (4/4): finalização
Motivação:   D-01
Arquivos:    apps/social/services/publishing/finalize.py (novo), apps/social/tasks.py
O que muda:  extrair consolidação de resultado, chamada a posting_state (já de R-06),
             limpeza de mídia local, agendamento do primeiro comentário e métricas
Não muda:    o dict de retorno de _run_post_to_platforms, campo por campo
Pré-requisito: R-10, R-11
PR:          ~250 linhas · 3 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: R-04 verde; R-03 verde
Verificação pós-deploy: publish_duration_ms com distribuição equivalente à semana anterior
Risco:       médio
Reversão:    rollback simples
Esforço:     6h
Ganho:       _run_post_to_platforms cai de 1.189 para ~120 linhas de orquestração legível
```

```
[R-13] Emagrecer apps/social/tasks.py para orquestração fina
Motivação:   D-01 — fechar o ciclo
Arquivos:    apps/social/tasks.py, apps/social/services/publishing/*
O que muda:  mover os auxiliares que sobraram (_youtube_day_video_index,
             _replace_ambiguous_short_slot:608, _sync_factory_posting_schedule:405)
             para services/; remover os 18 imports dentro de função, que a essa altura
             já não têm ciclo para quebrar
Não muda:    nenhum comportamento; as 5 tasks Celery mantêm nome, assinatura e fila
Pré-requisito: R-12
PR:          ~300 linhas movidas · ~6 arquivos
Produção:    transparente
Deploy:      deploy normal. ATENÇÃO: nomes de task registrados no Celery NÃO podem mudar
             — há tasks agendadas no beat e possivelmente mensagens em voo na fila.
Como validar: suíte verde; conferir que config/celery.py e CELERY_TASK_ROUTES continuam
             resolvendo todos os nomes de task
Verificação pós-deploy: nenhuma task no beat pode ficar com "unregistered task"; olhar
             a fila publish nos primeiros 30 min
Risco:       médio — o risco real aqui é nome de task, não lógica
Reversão:    rollback simples
Esforço:     4h
Ganho:       tasks.py de 4.035 → ~250 linhas. Fim do god module.
```

```
[R-14] Extrair ações de inventário das views para apps/jobs/services/inventory_actions.py
Motivação:   D-04 — regra de negócio e transação dentro de handler HTTP
Arquivos:    apps/jobs/services/inventory_actions.py (novo), apps/api/views.py,
             apps/api/tests/test_inventory_actions.py (novo — é o CT-3)
O que muda:  mover remove_awaiting (apps/api/views.py:1189-1248) e retry_posting
             (:1250-1376) para funções de serviço. As views viram ~10 linhas: validar
             entrada, chamar serviço, serializar resposta.
Não muda:    contrato HTTP — mesma URL, mesmo payload, mesmos códigos de status,
             mesmas chaves no JSON de resposta. O frontend não muda.
Pré-requisito: R-07 (o site views.py:1502 já foi migrado)
PR:          ~300 linhas · 3 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: CT-3 escrito ANTES do movimento, passando contra o código atual, e
             passando de novo depois sem edição
Verificação pós-deploy: nenhum erro 500 nas rotas remove-awaiting e retry-posting
Risco:       baixo-médio
Reversão:    rollback simples
Esforço:     6h
Ganho:       a ação passa a ser chamável de management command e de task, sem duplicar
```

```
[R-15] Quebrar apps/api/views.py em pacote views/
Motivação:   D-04 — 2.522 linhas, 26 classes, 38 alterações/ano gerando conflito
Arquivos:    apps/api/views/__init__.py + 5-6 módulos por domínio; apps/api/views.py removido
O que muda:  APENAS movimentação. Cada viewset vai para o módulo do seu domínio;
             __init__.py reexporta todos os nomes para apps/api/urls.py não mudar.
             ZERO edição de corpo de método neste PR.
Não muda:    absolutamente nada de comportamento. Rotas idênticas.
Pré-requisito: R-14 (para mover código já enxuto, não o inchado)
PR:          ~2.500 linhas MOVIDAS · ~8 arquivos — grande em linhas, trivial em revisão
             (git detecta como rename/move; revisar com --color-moved)
Produção:    transparente
Deploy:      deploy normal
Como validar: suíte de api verde; manage.py check; comparar a lista de rotas
             registradas antes e depois (django-extensions show_urls ou script simples)
Verificação pós-deploy: smoke test das rotas principais
Risco:       baixo — desde que seja movimentação pura. Se qualquer lógica for editada
             neste PR, o item foi executado errado.
Reversão:    rollback simples
Esforço:     3h
Ganho:       fim dos conflitos de merge em views.py; cada domínio com arquivo próprio
```

```
[R-16] Quebrar apps/api/serializers.py em pacote serializers/
Motivação:   D-04 — 1.048 linhas, 24 alterações/ano
Arquivos:    apps/api/serializers/__init__.py + módulos por domínio
O que muda:  movimentação pura, mesma técnica de R-15
Não muda:    nada
Pré-requisito: R-15 (mesma técnica, valida o padrão primeiro no caso maior)
PR:          ~1.050 linhas movidas · ~6 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: apps/api/tests/test_serializers.py verde sem alteração
Verificação pós-deploy: nenhuma específica
Risco:       baixo
Reversão:    rollback simples
Esforço:     2h
Ganho:       simetria com views/; conflitos de merge reduzidos
```

```
[R-17] Centralizar configuração em settings (expand-contract)
Motivação:   D-08 — 57 os.getenv em 18 arquivos; leitura no import torna código intestável
Arquivos:    social_automation/settings.py + os 18 arquivos leitores, em 3 PRs
O que muda:  EXPAND-CONTRACT em 3 etapas, uma por PR:
             (a) adicionar a setting em settings.py com o MESMO default do getenv atual
             (b) migrar os leitores para settings.X — em lotes de ~6 arquivos por PR
             (c) remover os os.getenv órfãos
             Prioridade: era "apps/social/tasks.py:65-68 primeiro, é o que trava R-04".
             FEITO no lote 1 (#37) — e a justificativa caiu junto: a constante daquelas
             linhas era código morto, não um ramo travado. Ver L-9.
LOTE 1 CONCLUÍDO (#37): YOUTUBE_CHECK_CLIENT_ID/_SECRET/_REDIRECT_URI e
             GOOGLE_CLIENT_ID/_SECRET, em 3 leitores. Etapas (a), (b) e (c) juntas —
             como os 3 leitores foram migrados de uma vez, não houve janela de variável
             órfã, então o intervalo de 1 semana entre (b) e (c) não se aplicou.
             Restam ~52 os.getenv em ~15 arquivos para os próximos lotes.
Não muda:    nenhum default. Toda variável de ambiente hoje suportada continua
             funcionando com o mesmo nome e o mesmo valor padrão.
Pré-requisito: R-02
PR:          3 PRs de ~150 linhas cada · ~7 arquivos cada
Produção:    REQUER CUIDADO — ver seção 9
Deploy:      etapa (a) e (b) podem ir juntas por lote. A etapa (c) só depois de
             confirmar em produção que nenhuma variável ficou órfã. Intervalo mínimo
             sugerido entre (b) e (c): 1 semana.
Como validar: teste que compara, para cada variável migrada, settings.X com o
             os.getenv equivalente sob o mesmo ambiente
Verificação pós-deploy: conferir no ambiente real que nenhum comportamento dependente
             de env mudou (FFmpeg presets, filas Celery, flags do YouTube)
Risco:       médio — errar um default silenciosamente muda comportamento em produção
             sem quebrar teste. Mitigação: o PR (a) exige diff lado a lado
             getenv-atual × setting-nova, revisado variável por variável.
Reversão:    rollback simples nas etapas (a) e (b). Depois de (c), reverter exige
             restaurar os getenv — por isso o intervalo de 1 semana.
Esforço:     6h no total
Ganho:       uma fonte da verdade de configuração; override_settings volta a funcionar
```

```
[R-18] Separar prompts do cliente em apps/auto_cuts/prompts/
Motivação:   D-09 — ~1.235 linhas de prompt e tabela de preço dentro do cliente HTTP
Arquivos:    apps/auto_cuts/prompts/{__init__,system,templates,vocabulary}.py (novos),
             apps/auto_cuts/services/grok_pricing.py (novo), apps/auto_cuts/services/grok.py
O que muda:  mover grok.py:21-1235 — GROK_PRICING, CTR_WORDS_*, FORBIDDEN_WORDS_*,
             ALL_THEME_CATEGORIES, ANTI_AUTOMATION_RULES_*, METADATA_SAFETY_RULES_*,
             SYSTEM_PROMPT*, CHUNKS_PROMPT_TEMPLATE* — para os novos módulos.
             Movimentação pura: NENHUM texto de prompt é reescrito.
Não muda:    byte por byte, os prompts enviados ao LLM são idênticos
Pré-requisito: nenhum — independente das ondas 0-2, pode rodar em paralelo
PR:          ~1.240 linhas movidas · ~6 arquivos
Produção:    transparente
Deploy:      deploy normal
Como validar: teste que faz hash dos prompts montados antes e depois e compara. Se um
             único caractere mudou, o PR está errado.
Verificação pós-deploy: custo médio por análise e taxa de retry do Grok estáveis
Risco:       baixo — protegido pelo teste de hash
Reversão:    rollback simples
Esforço:     4h
Ganho:       grok.py de 2.057 → ~600 linhas; ajuste de prompt vira diff legível
```

```
[R-19] Fatiar os fluxos de auto_cuts para services/
Motivação:   D-11 — analyze_auto_cuts_task (652 linhas) e finalizar_auto_cut_task (556)
Arquivos:    apps/auto_cuts/services/analysis_flow.py e finalization_flow.py (novos),
             apps/auto_cuts/tasks.py, testes de caracterização (CT-4)
O que muda:  EM 4 PRs: (a) CT-4 characterization; (b) extrair analyze_auto_cuts_task;
             (c) extrair finalizar_auto_cut_task; (d) remover os 27 imports dentro
             de função
Não muda:    estado final da análise, tasks enfileiradas, arquivos gerados
Pré-requisito: R-02. Independente da onda 1 — pode rodar em paralelo por outra pessoa.
PR:          4 PRs de ~300-400 linhas cada
Produção:    transparente
Deploy:      deploy normal. Mesma atenção de R-13 quanto a nomes de task no beat.
Como validar: CT-4 verde; um vídeo real processado ponta a ponta em staging
Verificação pós-deploy: taxa de sucesso do pipeline de cortes nas primeiras 48h
Risco:       médio — pipeline longo, com FFmpeg e LLM, difícil de reproduzir em teste
Reversão:    rollback simples
Esforço:     2 dias
Ganho:       tasks.py de 2.111 → ~300 linhas; fim dos 27 imports circulares
```

```
[R-20] Substituir os 'except Exception: pass' mais arriscados por falha observável
Motivação:   D-10 — 52 pontos engolem erro sem log, métrica ou sinal
Arquivos:    top 10 sites, priorizados por: mexe em disco, mexe em estado, ou responde
             ao usuário. Começar por apps/api/views.py:1219-1229.
O que muda:  cada bloco passa a registrar via log_event (a infra já existe em
             apps/jobs/logging_utils.py) e, onde o resultado é reportado ao usuário,
             refletir a falha no payload em vez de mentir o contador
Não muda:    o fluxo não passa a levantar exceção — continua tolerante a falha.
             Muda a VISIBILIDADE, não o controle de fluxo.
             ATENÇÃO: onde o payload da resposta muda (contador que mentia), isso é
             mudança de comportamento observável — vai marcado e em PR próprio.
Pré-requisito: R-14 (para não mexer em código que vai sair da view)
PR:          2 PRs de ~150 linhas · ~5 arquivos cada
Produção:    transparente
Deploy:      deploy normal
Como validar: teste que força a falha e verifica que o evento foi logado
Verificação pós-deploy: novos eventos de erro devem APARECER nos logs — o volume
             subindo é sinal de sucesso, não de regressão. Comunicar isso a quem
             monitora, senão vira falso alarme.
Risco:       baixo
Reversão:    rollback simples
Esforço:     4h
Ganho:       falhas de disco e de rede param de ser invisíveis
```

---

## 8. Sequenciamento

### Onda 0 — Destravar e medir · ~18h

**R-01 → R-02 → (R-03 ‖ R-04 ‖ R-05)**

R-01 e R-02 são sequenciais e bloqueiam tudo. R-03, R-04 e R-05 rodam em paralelo — R-05
inclusive é independente de todo o resto e pode ir a qualquer momento.

> Sem a onda 0 completa, **nenhum item de outra onda deve começar.** É a única regra rígida
> de sequenciamento deste plano.

### Onda 1 — Fluxo de publicação · ~45h — *é aqui que o projeto se paga*

**R-06 → R-07 → R-08 → R-09 → (R-10 → R-11) → R-12 → R-13**

Cadeia quase totalmente sequencial, porque todos tocam `apps/social/tasks.py` e PRs
paralelos no mesmo arquivo se destroem no merge. R-10 e R-11 são logicamente
independentes, mas devem ir em série pelo mesmo motivo.

⚠ **Ponto de maior atenção do projeto inteiro:** R-06 e R-07 alteram a atomicidade da
transição de publicação. É a mudança mais valiosa e a mais delicada. Não emendar com R-08
no mesmo dia — deixar R-06+R-07 rodando em produção por **pelo menos 48h** antes de seguir.

### Onda 2 — Camada de API · ~11h

**R-14 → R-15 → R-16**

Depende de R-07 (onda 1). Pode rodar em paralelo com o fim da onda 1 se for outra pessoa —
os arquivos não se cruzam depois de R-14.

### Onda 3 — Config, LLM e higiene · ~30h

**R-17 ‖ R-18 ‖ R-19 ‖ R-20**

Quase tudo paralelizável. Exceções:
- **R-17 etapa (a)** deve idealmente ser antecipada para a onda 0, só para
  `apps/social/tasks.py:65-68` — é o que destrava o ramo faltante de R-04.
- **R-18 e R-19** são o único par que toca `apps/auto_cuts` — R-18 primeiro (é mais
  simples e mais seguro), R-19 depois.
- **R-20** depende de R-14.

### Ponto sem volta

Só um: **R-17 etapa (c)** (remoção dos `os.getenv` órfãos). Depois dela, reverter exige
restaurar código, não só dar rollback. Por isso o intervalo obrigatório de 1 semana entre
(b) e (c).

Nenhum outro item tem ponto sem volta — não há migração de schema, remoção de endpoint,
nem mudança de contrato em lugar nenhum deste plano.

---

## 9. Impacto em produção

### Contagem

| Nível | Itens |
| --- | --- |
| **Transparente** | 16 |
| **Requer cuidado** | 2 — R-06, R-17 |
| **REQUER PARADA** | **0** |

> ## ✅ Nenhum item deste plano exige parada de produção.
>
> Isso não foi sorte: foi restrição de projeto. O plano foi construído **sem nenhuma
> alteração de schema, de contrato HTTP ou de formato de mensagem de fila** — que são as
> três fontes usuais de indisponibilidade. Onde havia tentação de mexer em modelo
> (unificar status em enum, por exemplo), o item foi **deliberadamente deixado de fora**
> (ver seção 12).

### Detalhe dos itens "requer cuidado"

#### R-06 — introdução de `transaction.atomic()` na transição de publicação

- **Sem interrupção.** O sistema fica no ar o tempo todo.
- **O que exige cuidado:** a transação passa a segurar lock em 4 tabelas
  (`ScheduledPost`, `FactoryPostingSchedule`, `VideoInventoryItem`, `PostedVideoLog`) por
  alguns milissegundos. Sob volume alto de publicação simultânea, aumenta a chance de
  contenção.
- **Procedimento:** deploy em janela de baixo tráfego de publicação. Antes do merge,
  revisar o diff confirmando que **nenhuma chamada de rede** (YouTube API, Upload-Post)
  ficou dentro do bloco `atomic` — é o único jeito de isso virar problema sério.
- **Convivência:** workers na versão antiga e na nova escrevem os mesmos campos com os
  mesmos valores. Rolling deploy é seguro.
- **Ponto de verificação:** por 24h, a consulta de consistência
  (`ScheduledPost` DONE cujo `VideoInventoryItem` não está `POSTED`) deve retornar 0, e
  não pode haver aumento de deadlock no Postgres.
- **Rollback:** simples, a qualquer momento. Volta ao comportamento não atômico.

#### R-17 — centralização de configuração

- **Sem interrupção.**
- **O que exige cuidado:** um default digitado errado muda comportamento em produção
  **sem quebrar nenhum teste**. É o risco silencioso clássico.
- **Procedimento:** expand-contract em 3 etapas, com o PR (a) revisado variável por
  variável em diff lado a lado (`os.getenv` atual × setting nova). Etapa (c) só depois de
  **1 semana** de (b) rodando em produção.
- **Ponto de verificação:** conferir no ambiente real os presets de FFmpeg, as filas do
  Celery e as flags do YouTube antes de liberar a etapa (c).
- **Rollback:** simples em (a) e (b). Depois de (c), exige restaurar código.

### Regra que não se negocia

Nenhum item classificado como REQUER PARADA é executado sem autorização explícita, na
hora, com o procedimento à vista. **Hoje não há nenhum.** Se durante a execução ficar
claro que um item classificado como transparente na verdade exige parada — **pare
imediatamente, avise e reclassifique.** Não siga "porque já começou".

---

## 10. Métricas de sucesso

| Métrica | Hoje | Meta | Como medir |
| --- | --- | --- | --- |
| Maior arquivo Python | 4.035 linhas | **< 500** | script da linha de base |
| Maior função | 1.189 linhas | **< 150** | script da linha de base |
| Arquivos > 500 linhas | 15 | **≤ 8** | idem |
| Funções ≥ 80 linhas | 48 | **≤ 25** | idem |
| Cópias da transição "POSTED" | 5 | **1** | `grep -rn 'status = "POSTED"' apps/` |
| Transições multi-modelo sem `atomic` | 2 confirmadas | **0** | revisão de `posting_state.py` |
| % do código medido pelo portão | ~~7,5%~~ → **100%** ✅ (R-02) | > 90% | `pyproject.toml` |
| Cobertura real (linhas) | **40,81%** (medida em R-02) | catraca — só sobe | relatório do pytest |
| Cobertura de `auto_cuts/tasks.py` | **7%** | **> 40%** (via R-19/CT-4) | relatório do pytest |
| Cobertura de `api/views.py` | **21%** | **> 50%** (via R-14/CT-3) | relatório do pytest |
| Cobertura de `social/tasks.py` | ~~41%~~ → **49%** (R-03/R-04 feitos) | **> 65%** (o resto vem com a fatiagem, R-09 a R-13) | relatório do pytest |
| Imports dentro de função (`apps.*`) | 96 | **< 20** | `grep -rn '^\s\+from apps\.'` |
| `os.getenv` fora de settings | 57 | **0** | `grep -rn 'os.getenv' apps/` |
| `except Exception: pass` | 52 | **≤ 40** (top 10 tratados) | `grep -rA1 'except Exception'` |
| Violações de ruff | 0 | **0** (manter) | `ruff check .` |
| Tempo da suíte | 65s | **≤ 90s** | `manage.py test` |
| Testes do frontend no CI | não | **sim** | `ci.yml` |

### Métricas perceptíveis (as que realmente importam)

- **Commits de `fix` em `apps/social/`** nos 3 meses seguintes à onda 1, comparados aos 15
  dos 12 meses anteriores. É a prova direta de que o problema era estrutural.
- **`fix_youtube_posted_status.py` deixa de ser executado.** Se ninguém precisar rodar
  esse comando em 3 meses, R-06 e R-07 fizeram o trabalho.
- **Tempo para adicionar uma nova plataforma de publicação:** hoje significa editar uma
  função de 1.189 linhas. Depois, um módulo novo em `publishing/`.
- **Conflitos de merge em `views.py` e `tasks.py`:** hoje são certos entre PRs
  simultâneos; depois de R-13/R-15 devem sumir.

---

## 11. Riscos

| Risco | Probabilidade | Mitigação |
| --- | --- | --- |
| **Regressão silenciosa no fluxo de publicação** — o pior cenário: vídeo publicado duas vezes ou nunca | Média | Onda 0 inteira antes de qualquer coisa (R-03/R-04). Ondas 1 em passos de ≤400 linhas. R-06 e R-07 com 48h de observação antes de seguir. |
| **Diferenças escondidas entre as 5 cópias do D-02** — unificar pode mudar comportamento sem ninguém notar | **Alta** | R-03 fixa cada cópia individualmente antes de R-07. Toda divergência encontrada é documentada no PR e vira decisão consciente, não efeito colateral. |
| **Refatoração fica pela metade** — `tasks.py` acaba dividido em dois lugares, pior que antes | Média | Ondas fechadas com marco explícito no checklist. Se a onda 1 for interrompida, o ponto seguro de parada é **depois de R-13** ou **depois de R-07** — nunca no meio de R-09..R-12. |
| **Conflito com trabalho em andamento** — `apps/social/tasks.py` muda 42×/ano | **Alta** | Onda 1 é sequencial de propósito. Combinar congelamento informal de features em `apps/social/` durante a onda 1 (~2 semanas), ou aceitar rebases frequentes. |
| **Default errado em R-17 muda produção sem quebrar teste** | Média | Expand-contract com revisão variável a variável e 1 semana de intervalo antes da remoção. |
| **Nome de task Celery alterado sem querer** (R-13, R-19) — mensagens em voo viram `unregistered task` | Baixa | Checagem explícita no critério de validação dos dois itens. Nomes de task são contrato de fila. |
| **Prazo estoura e o projeto é abandonado no meio** | Média | Ondas 0 e 1 entregam o grosso do valor em ~63h. As ondas 2 e 3 são explicitamente opcionais. Parar depois da onda 1 é resultado bom, não fracasso. |
| **Conhecimento concentrado** — o fluxo de publicação parece ter um único dono | Desconhecida | Os characterization tests de R-03/R-04 funcionam como documentação executável. É um efeito colateral valioso da onda 0. |

---

## 12. O que NÃO refatorar

Olhei e decidi deixar quieto:

- **`social_automation/settings.py`** — 353 linhas, muda 24×/ano, **0 commits de `fix`**.
  Muda porque configuração muda, não porque está errado. Está bem organizado. Não tocar.
- **`apps/jobs/services/`** — 12 módulos, a área mais bem coberta do projeto (1.387 LOC de
  teste). A camada de serviço aqui **funciona**. É o modelo a copiar, não a consertar.
- **`apps/brands/models.py`, `apps/jobs/models.py`** (586 e 743 linhas) — grandes, mas são
  models Django: campos, `Meta`, `__str__`, properties. Densidade de lógica baixa, 2 fixes
  cada em 12 meses. Quebrar em vários arquivos custaria migrations e ganharia pouco.
- **Unificar campos `status` em `TextChoices`/enum** — seria a evolução natural depois de
  R-06, e é a única coisa neste diagnóstico que exigiria migration. **Deliberadamente
  fora**, porque introduziria o único item "requer parada" do plano em troca de ganho
  estético. Se um dia valer a pena, é projeto próprio, com expand-contract de coluna.
- **`apps/cuts`, `apps/mediahub`** — 53 e 21 linhas de model, quase sem teste, mas também
  quase sem mudança e sem bug. Código estável e minúsculo não é prioridade.
- **`frontend/src/pages/CortesAutomaticos.jsx`** (2.387 linhas) e `api.js` (1.183) — são
  problema real, mas de **outra disciplina**. Misturar refatoração React com refatoração
  Django no mesmo projeto dobra a superfície de risco e ninguém revisa bem os dois. Merece
  projeto próprio, depois deste. Aqui entra só R-05 (rodar os testes que já existem).
- **`apps/api/factory_youtube_dashboard.py`** (936 linhas) — grande, mas é agregação de
  leitura, tem 616 linhas de teste dedicado e **0 commits de `fix`**. Feio e estável não é
  prioridade.
- **Migrations** — nunca. Nem para "organizar".

### Reescrever em vez de refatorar?

**Não.** Em nenhuma área. O sistema está em produção, o domínio (publicação em redes
sociais com reconciliação, idempotência e quota) tem centenas de regras aprendidas na
prática que não estão escritas em lugar nenhum além do próprio código, e a base tem CI,
ADRs e observabilidade funcionando. Reescrever perderia esse conhecimento e ganharia
nada. A abordagem incremental deste plano é a correta.

---

## 13. Checklist de acompanhamento

```
Status: em andamento
Progresso: 5/21 itens concluídos (R-02, R-03, R-04, R-05, R-19)
           8 IMPLANTADOS em 2026-08-13, em verificacao de 24h
             (R-01, R-21, R-22, R-23, R-06, R-07, R-17 lote 1, R-18)
           1 em andamento por lotes (R-17 — lotes 1, 2 e 3 feitos, ~34 getenv restantes)
           ✅ Onda 0 fechada · ✅ D-02 fechado (R-06+R-07) · ✅ D-09 fechado (R-18)
           ▶ próximo: R-08 (liberado em 15/08)
           suite: 288 -> 416 testes · cobertura 40,8% -> 44,04% local
           atualizado em 2026-08-14
Itens que exigem parada de produção: 0
```

> **O backlog cresceu de 18 para 21 itens.** Nenhum dos três acréscimos estava no plano
> original — todos são bugs de produção encontrados **pelos characterization tests**, que
> era exatamente o objetivo da Onda 0:
>
> | Item | Encontrado em | O que é |
> | --- | --- | --- |
> | **R-21** | R-03 | `update_fields` com campo inexistente — corrigido, PR #30 |
> | **R-22** | R-04 | `post.job.output` levanta exceção e torna uma guarda inalcançável — corrigido, PR #33 |
> | **R-23** | R-04 | erro da tentativa anterior sobrevive num post `DONE` — corrigido, PR #34 |
>
> Estão registrados na seção 7 junto com os demais. **Os três já estão corrigidos e
> mergeados**, faltando só o deploy. O padrão que funcionou nos três: o characterization
> test entra primeiro afirmando o comportamento **errado** de hoje, e a correção o inverte
> — assim o bug aparece no diff da correção, não fica implícito nela.

> **Nota de honestidade do contador:** R-02 e R-05 só alteram CI e configuração de teste —
> para eles, merge **é** o deploy, e estão concluídos. R-01 é código de aplicação: está
> mergeado em `develop` com CI verde, mas **ainda não foi implantado em produção**, então
> continua em aberto até que isso aconteça e a verificação pós-deploy seja feita.
>
> PRs mergeados: [#25](https://github.com/Cascapera/social_automation/pull/25) (R-01),
> [#29](https://github.com/Cascapera/social_automation/pull/29) (R-02),
> [#27](https://github.com/Cascapera/social_automation/pull/27) (R-05),
> [#28](https://github.com/Cascapera/social_automation/pull/28) (este documento).
> `develop` em `3df0074`, CI verde.
>
> [#26](https://github.com/Cascapera/social_automation/pull/26) foi fechada sem merge: era
> a PR original do R-02, empilhada sobre a branch do R-01, e o GitHub a fechou
> automaticamente quando aquela branch foi apagada no merge — sem permitir reabrir nem
> reapontar a base. Substituída pela #29, mesmo commit. **Lição para as ondas seguintes:
> em PR empilhada, não usar `--delete-branch` no merge da base.**

### ⚠ Itens que exigem parada de produção

**Nenhum.** Se durante a execução algum item se revelar impossível sem indisponibilidade,
**pare, avise e reclassifique** antes de prosseguir.

---

### Onda 0 — Destravar e medir  ·  ~18h

- [ ] **R-01** · Corrigir `_NoOpMetric.observe()` ausente
      risco: baixo · 30min · produção: transparente · PR: ~40 linhas / 2 arquivos
      🔧 **é correção de bug, não refatoração — PR próprio com prefixo `fix()`**
  - [x] Teste de regressão escrito (simula ImportError) e falhando antes da correção
  - [x] Correção aplicada
  - [x] Suíte completa verde (7 erros → 0)
  - [x] Lint verde
  - [x] PR aberto e revisado — [#25](https://github.com/Cascapera/social_automation/pull/25), CI verde, mergeado
  - [ ] Implantado em produção
  - [ ] Verificado em produção — `publish_duration_ms` presente em `/metrics`
  - [x] Commitado — `eb58699` (mergeado em `develop` via `47eb3f2`)
  - Status: **em andamento — aguardando deploy de produção** · Notas: dos 7 erros da linha de base,
    6 eram este bug. A correção colapsou `_NoOpChild` e `_NoOpMetric` numa classe só, com
    `labels()` retornando `self` — assim os caminhos com e sem label não podem divergir de
    novo (era essa divergência, não a falta de um método, a causa raiz). Teste anti-drift
    varre todas as métricas do módulo exigindo `inc`/`observe` com e sem label. O 7º erro
    era `prometheus_client` faltando no `.venv` local — dependência já declarada em
    `requirements.txt`, venv sincronizado. Suíte: **294 testes, 60s, OK**.

- [x] **R-02** · Portão de cobertura sobre o código real, com catraca
      risco: baixo · 2h · produção: transparente (só CI) · PR: ~25 linhas / 2 arquivos
      pré-requisito: R-01
  - [x] Cobertura real medida e registrada aqui nas notas
  - [x] `pyproject.toml` ajustado com o piso medido
  - [x] Suíte verde com o novo portão (local)
  - [x] CI verde com o novo portão
  - [x] PR aberto e revisado — [#29](https://github.com/Cascapera/social_automation/pull/29), mergeado
  - [x] Commitado — `5b7fdf4` (mergeado em `develop` via `3df0074`)
  - Status: **concluído** · Notas: **cobertura real medida = 40,81%**
    (12.844 statements, 7.061 sem cobertura, testes/migrations omitidos). Com os arquivos
    de teste incluídos o número subia para 51% — por isso o `omit`. Portão trocado de
    "70% sobre 12 módulos (7,5% do código)" para "40,8% sobre `apps` + `social_automation`
    inteiros". README corrigido: afirmava "~70%+ core modules". Por arquivo crítico:
    `auto_cuts/tasks.py` **7%**, `api/views.py` **21%**, `grok.py` **30%**,
    `social/tasks.py` **41%**, `api/serializers.py` **43%**.

- [x] **R-22** · Corrigir guarda inalcançável de "Job sem vídeo final"
      risco: baixo · 30min · produção: **muda comportamento observável**
      PR: ~30 linhas / 2 arquivos · pré-requisito: R-04
      🔧 **correção de bug, não refatoração — PR próprio `fix()`** · item novo, não estava no plano
  - [x] Teste caracterizador já no repo (veio do R-04), invertido de `assertRaises` para
        `FAILED` — falha com `RelatedObjectDoesNotExist` antes da correção, passa depois
  - [x] Correção aplicada em `tasks.py:2498` (`try/except RenderOutput.DoesNotExist`)
  - [x] Suíte completa verde · Lint verde
  - [x] PR aberto e revisado — [#33](https://github.com/Cascapera/social_automation/pull/33), CI verde, mergeado
  - [x] Commitado — `ffcfc27` (mergeado em `develop` via `a702796`)
  - [ ] Implantado em produção
  - [ ] Verificado em produção — posts de job sem render viram FAILED com motivo legível
  - Status: **em andamento — aguardando deploy de produção** · Notas: o acesso ao OneToOne
    reverso levantava antes da guarda, então o post ficava preso em PENDING e a task
    queimava as 3 tentativas sem registrar nada no post. Depois do deploy esses itens
    passam a aparecer como FAILED — é o diagnóstico ficando visível, não uma regressão.

- [x] **R-23** · Limpar `post.error` ao concluir uma publicação com sucesso
      risco: baixo · 20min · produção: transparente, corrige exibição
      PR: ~25 linhas / 2 arquivos · pré-requisito: R-04
      🔧 **correção de bug, não refatoração — PR próprio `fix()`** · item novo, não estava no plano
  - [x] Teste caracterizador já no repo (veio do R-04), invertido de `"erro anterior"`
        para `""` — falha antes da correção, passa depois
  - [x] Correção aplicada em `tasks.py:3568`
  - [x] Teste novo do ramo com warnings, que garante a cláusula "não muda": o warning do
        publisher continua virando o texto de `post.error` mesmo com `status=DONE`
  - [x] Suíte completa verde · Lint verde
  - [x] PR aberto e revisado — [#34](https://github.com/Cascapera/social_automation/pull/34), CI verde, mergeado
  - [x] Commitado — `2322efa` (mergeado em `develop` via `7c316a4`)
  - [ ] Implantado em produção
  - [ ] Verificado em produção — posts DONE param de exibir erro no painel
  - Status: **em andamento — aguardando deploy de produção** · Notas: só afeta posts que
    passaram por pelo menos uma tentativa falha antes do sucesso. Como `"error"` está no
    `update_fields`, o texto antigo era regravado junto com `status=DONE`.

- [x] **R-21** · Corrigir `update_fields` com campo inexistente em `ScheduledPost`
      risco: baixo · 2h · produção: transparente no deploy, **muda comportamento**
      PR: ~157 linhas / 2 arquivos · pré-requisito: nenhum
      🔧 **correção de bug, não refatoração — PR próprio `fix()`** · item novo, não estava no plano
  - [x] Testes de regressão escritos e falhando antes da correção (3 de 4, via `git stash`)
  - [x] Correção aplicada nos dois sites
  - [x] Suíte completa verde · Lint verde
  - [x] PR aberto e revisado — [#30](https://github.com/Cascapera/social_automation/pull/30), CI verde, mergeado
  - [x] Commitado — `4b83424` (mergeado em `develop` via `d8ae450`)
  - [ ] Implantado em produção
  - [ ] Verificado em produção — `publish_reconciliation_failures_total` caindo
  - [ ] **Avisado quem acompanha os painéis** sobre o pico de itens virando POSTED
  - Status: **em andamento — aguardando deploy de produção** · Notas: `ScheduledPost` não
    tem `updated_at`, mas `tasks.py:838` e `:3837` o incluíam em `update_fields` →
    `ValueError` em toda chamada. Site 1 abortava as duas tasks de reconciliação do
    YouTube já no primeiro vídeo confirmado (dentro de `except Exception` amplo, aparecia
    como "reconciliação falhou"). Site 2 impedia a gravação de `first_comment_posted`,
    quebrando a idempotência prometida na docstring — com `acks_late` + `max_retries=2`,
    o comentário fixado podia ser postado mais de uma vez.

- [x] **R-03** · Characterization tests da máquina de estados (CT-1)
      risco: baixo · 6h · produção: transparente · PR: 640 linhas / 1 arquivo
      pré-requisito: R-02, R-21
  - [x] Testes escritos cobrindo as 5 cópias — 31 testes
  - [x] Passam contra o código atual **sem alterá-lo**
  - [x] Divergências entre as cópias registradas nas notas
  - [x] Suíte completa verde · Lint verde
  - [x] PR aberto e revisado — [#31](https://github.com/Cascapera/social_automation/pull/31), CI verde, mergeado
  - [x] Commitado — mergeado em `develop`
  - Status: **concluído** · Notas: **as 5 cópias concordam em apenas 3 campos**
    (`schedule.status=DONE`, `item.status=POSTED`, `item.last_error=""`). Tudo além disso
    diverge — ver as 5 divergências abaixo, que são o insumo do R-07.
    `test_transition_is_not_atomic_today` documenta o D-03 e **deve ser invertido pelo R-06**.

- [x] **R-04** · Characterization tests de `_run_post_to_platforms` (CT-2)
      risco: baixo · 1d · produção: transparente · PR: ~470 linhas / 3 arquivos
      pré-requisito: R-02
  - [x] Caminho feliz coberto (5 testes: estado final, fingerprint, ids do provedor,
        attempt log, contrato de chamada)
  - [x] Guardas de saída antecipada cobertas — **9, não 6**: a contagem original omitia as
        duas guardas do ramo de corte (`tasks.py:2512` e `:2517`) e a de post sem origem
  - [x] Slot expirado e factory pausada cobertos (6 testes, incluindo o contraste entre
        slot que morre durante a pausa e slot que sobrevive)
  - [x] Gap conhecido (`YOUTUBE_CHECK_CLIENT_ENABLED`) documentado no topo do arquivo
  - [x] Suíte completa verde · Lint verde
  - [x] Catraca de cobertura subida: 40,8% → **42,0%** (medido 42,03% no CI)
  - [x] PR aberto e revisado — [#32](https://github.com/Cascapera/social_automation/pull/32), CI verde, mergeado
  - [x] Commitado — `b727d4a` (mergeado em `develop` via `575920f`)
  - Status: **concluído** · 22 testes novos.
    Notas: dois bugs de produção descobertos ao escrever os testes, registrados como
    **R-22** e **R-23** na seção 7. Ambos estão caracterizados (o teste afirma o
    comportamento errado de hoje) e o teste correspondente aponta o item que vai
    invertê-lo.

- [x] **R-05** · Rodar os testes do frontend no CI
      risco: baixo · 1h · produção: transparente · PR: ~3 linhas / 1 arquivo
      pré-requisito: nenhum — pode ir a qualquer momento
  - [x] `npm test` adicionado ao job frontend
  - [x] Testes rodados localmente antes de ligar no CI — **8 passam, 0 falham**
  - [x] CI verde (se um teste estiver quebrado, correção em PR **separado**)
  - [x] PR aberto e revisado — [#27](https://github.com/Cascapera/social_automation/pull/27), mergeado
  - [x] Commitado — `aeac2bf` (mergeado em `develop` via `c283b53`)
  - Status: **concluído** · Notas: os 8 testes
    (`youtubeSummaryCache`, `factoryWeeklySchedule`) passam hoje — o risco de "teste
    quebrado há meses" não se materializou. `install`, `test` e `build` viraram passos
    separados no job para que falha de teste apareça como falha de teste, não perdida no
    log do build. Item independente: não depende de nada e nada depende dele.

- [ ] **Onda 0 concluída** — suíte verde, cobertura real conhecida, rede montada
      🚧 **Nenhum item de outra onda começa antes desta caixa estar marcada.**

---

### Onda 1 — Fluxo de publicação  ·  ~45h  ·  *é aqui que o projeto se paga*

- [x] **R-06** · Criar `posting_state.py` — dono único das transições
      risco: médio · 6h · ⚠ **produção: requer cuidado** · PR: ~330 linhas / 5 arquivos
      pré-requisito: R-03
  - [x] Diff revisado: **nenhuma chamada de rede dentro do `atomic`** — as duas funções só
        fazem ORM. A query do `FactoryPostingSchedule` e o `parse_datetime` de
        `publish_at_raw` ficaram **fora** do bloco, encurtando o lock
  - [x] Teste anti-drift que trava essa regra: falha se alguém importar `requests`,
        `httpx`, `urllib` ou `socket` no módulo
  - [x] R-03 verde **sem alteração de asserção** — só mudou a linha de `import` e as
        referências de linha nos docstrings
  - [x] ⚠ **Uma inversão prevista pelo próprio R-03**: `test_transition_is_not_atomic_today`
        afirmava a ausência de atomicidade e dizia no docstring "R-06 deve INVERTER esta
        asserção". Virou `test_transition_is_atomic`
  - [x] Teste de abort no meio da transição (prova de atomicidade) — 6 testes novos em
        `test_posting_state_service.py`, abortando em passo **intermediário**, não no último
  - [x] Suíte completa verde (358 testes) · Lint verde · cobertura 42,30%
  - [x] PR aberto e revisado — [#35](https://github.com/Cascapera/social_automation/pull/35), CI verde, mergeado
  - [ ] Janela de baixo tráfego de publicação escolhida
  - [ ] Implantado
  - [ ] Verificado 24h: `ScheduledPost` DONE com inventário não-POSTED = 0
  - [ ] Verificado 24h: sem aumento de deadlock no Postgres
  - [x] Commitado — `1e85d53` (mergeado em `develop` via `995980d`)
  - Status: **em andamento — aguardando janela de deploy** · Notas: as duas funções saíram
    de `tasks.py` (cópia C) para o serviço, como `mark_posted` e `mark_still_scheduled`.
    Estado final dos 4 modelos idêntico; a única diferença observável é atomicidade.
    ⚠ **O módulo ainda não é o dono único** — as cópias A, B, D e E continuam de pé, e
    unificá-las é o R-07, bloqueado pelo L-7. Isso está dito no docstring do módulo para
    quem chegar nele sem o contexto do plano.

- [x] **R-07** · Apontar as 5 cópias para `posting_state`
      risco: médio · 4h · produção: transparente · PR: ~580 linhas / 6 arquivos
      pré-requisito: R-06
  - [x] As 4 decisões do L-7 tomadas (2026-08-13) e registradas na seção 15
  - [x] Divergências entre cópias documentadas no PR — as 5, com a decisão de cada uma
  - [x] `grep '\.status = "POSTED"' apps/` retorna **1** site (o serviço) — e agora tem
        **teste no CI** afirmando isso: `test_posting_state_is_the_only_writer_of_posted_status`
  - [x] R-03 verde — 7 testes **invertidos** de propósito, um por divergência, cada um
        com o docstring dizendo o que mudou. Nenhum apagado; as 5 entradas continuam
        sendo exercitadas
  - [x] Suíte completa verde (358 → **365** testes) · Lint verde · cobertura 42,21% local
  - [x] PR aberto e revisado — [#36](https://github.com/Cascapera/social_automation/pull/36)
  - [ ] Implantado
  - [ ] Verificado 24h — mesma checagem de consistência de R-06
  - [x] Commitado — `7e81e16`
  - Status: **em andamento — aguardando deploy** · Notas: as cópias A e B eram idênticas
    exceto pela deduplicação do log, então unificar **apagou o ramo** em vez de escolher
    um lado — `_sync_factory_posting_schedule` perdeu 60 linhas. O serviço ganhou
    `mark_item_posted()`, ancorada no `VideoInventoryItem`, porque a marcação manual
    parte do vídeo e não de uma tentativa de publicação — inclusive para item **sem
    schedule nenhum**, caso que `mark_posted()` recusa de propósito (a reconciliação
    depende dessa recusa). Custo aceito: uma query a mais por sync, porque o dono da
    transição busca o próprio schedule em vez de recebê-lo por parâmetro.
    ⚠ Efeito observável no deploy: a contagem de `PostedVideoLog` novos **cai** no ramo
    não-YouTube — é a duplicata sumindo, não perda de auditoria.
  - ⏸ **Deixar R-06+R-07 em produção por ≥48h antes de seguir para R-08.**

- [ ] **R-08** · Extrair `services/publish_targets.py`
      risco: baixo · 3h · produção: transparente · PR: ~200 linhas movidas / 2 arquivos
      pré-requisito: R-04
  - [ ] Diff revisado como **movimentação pura** (nenhum corpo de função editado)
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **R-09** · Fatiar `_run_post_to_platforms` (1/4): preflight
      risco: médio · 6h · produção: transparente · PR: ~250 linhas / 3 arquivos
      pré-requisito: R-04, R-08
  - [ ] Cada saída antecipada devolve o mesmo dict de hoje
  - [ ] R-04 verde **sem nenhuma alteração nos testes**
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Verificado — `publish_attempts_total` / `publish_failures_total` estáveis
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **R-10** · Fatiar (2/4): publicação nativa YouTube
      risco: médio-alto · 8h · produção: transparente · PR: ~350 linhas / 3 arquivos
      pré-requisito: R-09
  - [ ] Payload ao YouTube e `external_ids` inalterados
  - [ ] R-04 verde · testes de `publishers/youtube.py` verdes
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Verificado 24h — taxa de sucesso de publicação no YouTube estável
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **R-11** · Fatiar (3/4): ramo Upload-Post
      risco: médio-alto · 8h · produção: transparente · PR: ~350 linhas / 3 arquivos
      pré-requisito: R-09 (na prática, depois de R-10 para não conflitar)
  - [ ] Chave de idempotência gerada inalterada
  - [ ] `test_publish_idempotency.py` e `test_upload_post_reconciliation.py` verdes **sem edição**
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Verificado 24h — `upload_post_unknown_results_total` não subiu
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **R-12** · Fatiar (4/4): finalização
      risco: médio · 6h · produção: transparente · PR: ~250 linhas / 3 arquivos
      pré-requisito: R-10, R-11
  - [ ] Dict de retorno idêntico, campo por campo
  - [ ] R-03 e R-04 verdes
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Verificado — `publish_duration_ms` com distribuição equivalente à semana anterior
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas: `_run_post_to_platforms` final = ____ linhas

- [ ] **R-13** · Emagrecer `apps/social/tasks.py` para orquestração fina
      risco: médio · 4h · produção: transparente · PR: ~300 linhas movidas / ~6 arquivos
      pré-requisito: R-12
  - [ ] **Nomes de task Celery inalterados** (contrato de fila)
  - [ ] `config/celery.py` e `CELERY_TASK_ROUTES` resolvem todas as tasks
  - [ ] Os 18 imports dentro de função removidos
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Verificado 30min — nenhuma `unregistered task` na fila `publish`
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas: `tasks.py` final = ____ linhas (era 4.035)

- [ ] **Onda 1 concluída** — máquina de estados com dono único e atômica, god module desfeito
      ✅ **Ponto de parada seguro.** Se o projeto parar aqui, já se pagou.

---

### Onda 2 — Camada de API  ·  ~11h

- [ ] **R-14** · Extrair ações de inventário para `apps/jobs/services/inventory_actions.py`
      risco: baixo-médio · 6h · produção: transparente · PR: ~300 linhas / 3 arquivos
      pré-requisito: R-07
  - [ ] CT-3 escrito **antes** do movimento e passando contra o código atual
  - [ ] Contrato HTTP inalterado (URL, payload, status, chaves do JSON)
  - [ ] CT-3 passa de novo depois, **sem edição**
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Verificado — sem 500 em `remove-awaiting` e `retry-posting`
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **R-15** · Quebrar `views.py` em pacote `views/`
      risco: baixo · 3h · produção: transparente · PR: ~2.500 linhas **movidas** / ~8 arquivos
      pré-requisito: R-14
  - [ ] **Zero edição de corpo de método** — se houve, o item foi executado errado
  - [ ] `__init__.py` reexporta todos os nomes; `urls.py` não muda
  - [ ] Lista de rotas registradas idêntica antes e depois
  - [ ] `manage.py check` limpo · Suíte completa verde · Lint verde
  - [ ] PR revisado com `--color-moved`
  - [ ] Implantado · smoke test das rotas principais
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **R-16** · Quebrar `serializers.py` em pacote `serializers/`
      risco: baixo · 2h · produção: transparente · PR: ~1.050 linhas movidas / ~6 arquivos
      pré-requisito: R-15
  - [ ] Movimentação pura
  - [ ] `test_serializers.py` verde **sem alteração**
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **Onda 2 concluída** — camada HTTP fina, regra de negócio em serviço

---

### Onda 3 — Config, LLM e higiene  ·  ~30h

- [ ] **R-17** · Centralizar configuração em settings (expand-contract, por lotes)
      risco: médio · 6h · ⚠ **produção: requer cuidado** · PRs de ~150 linhas
      pré-requisito: R-02
  - **Lote 1 — `YOUTUBE_CHECK_*` + `GOOGLE_CLIENT_*`** — [#37](https://github.com/Cascapera/social_automation/pull/37)
    - [x] Settings adicionadas, revisadas **variável por variável**
    - [x] 3 leitores migrados (`youtube_credentials`, `youtube_oauth`, `youtube_fetch`)
    - [x] Teste comparando `settings.X` × `os.getenv` equivalente — 9 casos
    - [x] Etapa (c) junto: nenhum `os.getenv("YOUTUBE_CHECK_*")` órfão, com anti-drift
          no CI. Sem janela de órfã porque os 3 leitores foram migrados de uma vez —
          por isso o intervalo de 1 semana entre (b) e (c) não se aplicou a este lote
    - [x] Suíte verde (365 → **374**) · Lint verde · cobertura 42,21% → **42,27%** local
    - [x] Commitado — `c516ebb`
    - [ ] Implantado · verificado: OAuth de factory-check funcionando
  - **Lote 2 — `WHISPER_*`** — [#43](https://github.com/Cascapera/social_automation/pull/43)
    - [x] `WHISPER_MODEL`, `WHISPER_DEVICE` e `WHISPER_DEBUG_GPU` em `settings`,
          revisadas **variável por variável**
    - [x] 5 leitores migrados (`subtitles` ×2, `jobs/tasks`, `analysis_flow`,
          `video_chunks`, `multiple_creator/tasks`)
    - [x] ⚠ **`WHISPER_MODEL` tinha dois defaults** — `small` na transcrição fatiada,
          `large-v3` na de passada única. Viraram duas settings sobre a mesma env var,
          em vez de uma só que apagaria a diferença em produção. Ver **L-12**
    - [x] Teste comparando `settings.X` × `os.getenv` equivalente — 10 casos, com o
          anti-drift verificado por mutação
    - [x] Etapa (c) junto: nenhum `os.getenv("WHISPER_*")` órfão. Sem janela de órfã
          porque os 5 leitores foram migrados de uma vez
    - [x] Suíte verde (399 → **409**) · Lint verde · cobertura 43,76% → **44,01%** local,
          **43,77% → 43,77% no CI** (os testes novos batem em `settings.py`, que o CI
          mede 7pp abaixo do local)
    - [x] Commitado — `2ab5a57`
    - [ ] Implantado · verificado: modelo do Whisper igual ao de antes nos dois caminhos
  - **Lote 3 — `LLM_*` / `XAI_*` / `GROK_*`** — [#44](https://github.com/Cascapera/social_automation/pull/44)
    - [x] 11 variáveis em `settings`, revisadas **variável por variável**
    - [x] Leitor único (`services/grok.py`) migrado — sem janela de órfã
    - [x] **A precedência continua em `grok.py`**: é lá que os avisos de depreciação de
          `XAI_API_KEY` e `GROK_MODEL` são emitidos, uma vez por chamada. No settings o
          aviso sairia uma vez só, no boot, onde ninguém lê
    - [x] ⚠ **Uma mudança de comportamento, documentada**: `LLM_MAX_SHORTS`/`_LONGS`
          usam `int()`. Lidas no settings, um valor inválido derruba o boot em vez de
          fazer cada análise falhar no meio da task. A falha muda de lugar — para melhor
    - [x] `test_llm_provider.py` reescrito: mesmas asserções, `override_settings` no
          lugar do `patch.dict(os.environ)`. Ganhou 2 contraprovas de precedência que
          antes não cabiam — inclusive a de que `LLM_MODEL` **não** cobre a chamada leve
    - [x] Teste de equivalência + anti-drift (`test_llm_settings.py`)
    - [x] Suíte verde (409 → **416**) · Lint verde · cobertura 44,01% → **44,04%** local
    - [x] Commitado — `dbaaf54`
    - [ ] Implantado · verificado: provider, modelo e custo por análise estáveis
  - **Próximos lotes** — restam ~34 `os.getenv` em ~11 arquivos
    - [ ] Lote 4 — `YTDLP_*`, `UPLOAD_POST_*` e o resto
    - [ ] `grep 'os.getenv' apps/` retorna 0
  - Status: **em andamento — lote 1 mergeado, aguardando deploy** · Notas: o lote 1
    encerrou o "gap do R-04" descobrindo que ele não existia — `YOUTUBE_CHECK_CLIENT_ENABLED`
    era **código morto**, não um ramo travado. Ver **L-9**.

- [x] **R-18** · Separar prompts do cliente em `apps/auto_cuts/prompts/`
      risco: baixo · 4h · produção: transparente · PR: 1.184 linhas movidas / 6 arquivos
      pré-requisito: nenhum — pode rodar em paralelo desde o início
  - [x] Teste de **hash dos prompts montados** antes × depois — **43 nomes, zero
        divergência**. Baseline capturado antes de qualquer alteração, contra o arquivo
        de 2.057 linhas
  - [x] Nenhum texto de prompt reescrito — recorte por **AST**, não por número de linha
  - [x] Suíte completa verde (374 → **377**) · Lint verde · cobertura 42,27% → **42,42%**
  - [x] PR aberto e revisado — [#38](https://github.com/Cascapera/social_automation/pull/38)
  - [ ] Implantado
  - [ ] Verificado — custo médio por análise e taxa de retry do Grok estáveis
  - [x] Commitado — `d8a1b37`
  - Status: **em andamento — aguardando deploy** · Notas: `grok.py` final = **897 linhas**
    (era 2.057). O teste de hash **ficou permanente** em vez de ser descartado depois de
    usar: mudança de prompt quebra o build e exige atualizar o hash no mesmo PR. É a outra
    metade do D-09 — separar arquivos resolveu o `git blame`; isto faz a mudança editorial
    aparecer na revisão. Verificado por mutação que pega alteração de 1 caractere.
    ⚠ **Segunda ocorrência do L-9**: `CTR_WORDS_*` e `FORBIDDEN_WORDS_*` (~60 linhas) não
    são lidas por ninguém. Movidas, não apagadas — são conteúdo editorial e a intenção de
    quem escreveu não está no código. Ver item aberto abaixo.

- [x] **R-19** · Fatiar os fluxos de `auto_cuts` para `services/` (4 PRs)
      risco: médio · 2d · produção: transparente · 4 PRs de ~300-400 linhas
      pré-requisito: R-02 (e R-18 antes, por tocar o mesmo app)
  - [x] PR (a) — CT-4 characterization, passando contra o código atual —
        [#40](https://github.com/Cascapera/social_automation/pull/40), **21 testes**
        (11 do `analyze`, 10 do `finalize`). Cobertura de `auto_cuts/tasks.py`: 7% → **21%**
  - [x] PR (b) — `analyze_auto_cuts_task` extraída para `services/analysis_flow.py`,
        em 8 funções com nome. Saíram junto os helpers compartilhados
        (`services/flow_common.py`) e os fluxos de cortes prontos, que são ramos da
        análise. CT-4 passou **sem mudar nenhuma asserção** — só os 3 alvos de
        `patch()` acompanharam o código de módulo
  - [x] PR (c) — `finalizar_auto_cut_task` extraída para
        `services/finalization_flow.py`. Os 20 parâmetros viram um `FinalizeOptions`
        congelado, calculado uma vez em vez de 16 locais soltos atravessando 6 etapas
  - [x] PR (d) — imports dentro de função removidos. **Três ficaram**, agora com o
        motivo escrito no código: o ciclo real `services → tasks` (enfileiramento), o
        `youtube_fetch` (importa `googleapiclient` no topo) e o `multiple_creator`
        (o `except ImportError` é o contrato da função)
  - [x] **Nomes de task Celery inalterados** — as duas tasks continuam em
        `apps.auto_cuts.tasks`, só o corpo mudou de módulo
  - [ ] Vídeo real processado ponta a ponta em staging
  - [x] Suíte completa verde · Lint verde
  - [ ] Implantado · verificado 48h — taxa de sucesso do pipeline de cortes estável
  - [x] Commitado — (a) `87ae7d5` · (b) `7c76c81`+`e1eccfd` · (c) `c1e26b1` · (d) `8779378`
  - Status: **concluído — falta deploy** · Notas: `auto_cuts/tasks.py` em **73 linhas**
    (era 2.111): as duas tasks e nada mais. O corpo virou três módulos —
    `analysis_flow.py`, `finalization_flow.py` e `flow_common.py`.
    Conferência mecânica nos dois PRs: o multiset de linhas do corpo antigo contra o
    das funções novas só difere em assinatura, plumbing e linhas de `return`/`opts.` —
    nenhuma instrução de negócio, mensagem de erro ou métrica mudou.
    O ganho previsto era "tasks.py de 2.111 → ~300 linhas"; ficou em 73.

  > ⚠ **Ler o docstring dos dois arquivos de teste antes de escrever o (b) e o (c).** Eles
  > registram três armadilhas que a extração quebra em silêncio:
  >
  > 1. **As mensagens de erro são contrato de tela.** "Nenhum vídeo encontrado (source ou
  >    upload)" e "Arquivo de vídeo não existe no disco" pedem ações **opostas** de quem
  >    lê — reenviar contra chamar o suporte. Unificá-las passa em `assert status == error`.
  > 2. **Falha de finalização NÃO vira `status="error"`.** Ela deixa a análise em
  >    `finalizing` com mensagem própria, porque `services/recovery.py` procura exatamente
  >    esse estado. "Melhorar" para `error` quebra o recovery sem quebrar teste.
  > 3. **`has_nvenc()` roda a cada finalize** (`tasks.py:1720`), antes de olhar se existe
  >    algum corte — um `ffmpeg -encoders` por execução. Foi o que reprovou o CT-4 no CI,
  >    que não tem o binário. Candidato a memoizar ou mover para perto de quem usa.

- [ ] **R-20** · Tornar observáveis os `except Exception: pass` mais arriscados (2 PRs)
      risco: baixo · 4h · produção: transparente · 2 PRs de ~150 linhas / ~5 arquivos
      pré-requisito: R-14
  - [ ] Top 10 sites priorizados (disco / estado / resposta ao usuário)
  - [ ] Onde o payload da resposta muda, marcado como **mudança de comportamento** e em PR próprio
  - [ ] Teste que força a falha e verifica o evento logado
  - [ ] **Avisar quem monitora**: volume de eventos de erro vai subir — é sucesso, não regressão
  - [ ] Suíte completa verde · Lint verde
  - [ ] PR aberto e revisado · Implantado
  - [ ] Commitado — `<hash>`
  - Status: não iniciado · Notas:

- [ ] **Onda 3 concluída** — configuração com fonte única, prompts separados, falhas visíveis

---

### Registro de execução

Uma linha por item concluído: data · o que mudou de fato · surpresas encontradas.

| Data | Item | O que mudou | Surpresa |
| --- | --- | --- | --- |
| 2026-08-11 | **R-01** | `_NoOpChild` e `_NoOpMetric` colapsadas numa classe só (`labels()` retorna `self`); teste anti-drift em `apps/common/tests/test_metrics_fallback.py`. Suíte 288→294 testes, 7 erros→0. Commit `eb58699`. | A causa raiz não era "falta o método `observe`" e sim **duas classes paralelas que podiam divergir**. Corrigir só o método deixaria a armadilha montada para a próxima. Também: 6 dos 7 erros eram o bug; só 1 era ambiente. |
| 2026-08-11 | **R-02** | Portão passou de 12 módulos (7,5% do código) para `apps` + `social_automation` inteiros, com `omit` de testes/migrations. Piso = 40,8% (catraca). README corrigido. Commit `5b7fdf4`. | A cobertura real (**40,81%**) é quase metade dos "70%" declarados — e `apps/auto_cuts/tasks.py`, com 2.111 linhas e 7 commits de `fix`, está em **7%**. Pior que o diagnóstico previa. |
| 2026-08-11 | **R-05** | `npm test` no CI, com `install`/`test`/`build` em passos separados. Commit `aeac2bf`, PR #27. | Nenhuma: os 8 testes passam. O risco previsto ("podem estar quebrados") não se materializou. |
| 2026-08-11 | **PRs #25–#29** | Os 4 PRs da Onda 0 abertos, CI verde, mergeados em `develop` (`3df0074`). | A PR do R-02 (#26) foi **fechada automaticamente** pelo GitHub quando a branch base (do R-01) foi apagada no merge — e não dá para reabrir nem reapontar a base de uma PR fechada. Teve que ser recriada como #29. Em PR empilhada, não usar `--delete-branch` no merge da base. |
| 2026-08-11 | **R-21** | `updated_at` removido de dois `update_fields` de `ScheduledPost` (`tasks.py:838` e `:3837`) + 4 testes de regressão. Commit `4b83424`, PR #30. | **Item que não existia no plano.** A cópia C da máquina de estados levantava `ValueError` em toda chamada — a reconciliação do YouTube **nunca marcava nada como POSTED**, e a idempotência do primeiro comentário nunca valeu. O `except Exception` amplo das tasks de reconciliação escondeu isso como "reconciliação falhou". Achado que só apareceu porque o R-03 obrigou a executar a função de verdade. |
| 2026-08-12 | **R-04** | 22 characterization tests de `_run_post_to_platforms`, passando contra o código atual sem alterá-lo. Catraca 40,8% → 42,0%. Commit `b727d4a`, PR #32. | Três surpresas. (1) São **9 guardas de saída antecipada, não 6** — a contagem do plano omitia as duas do ramo de corte e a de post sem origem. (2) Dois bugs de produção apareceram na primeira execução dos testes, viraram **R-22** e **R-23**. (3) A catraca subida pela medição **local** (42,23%) quebrou o CI, que mede 42,03%: `settings.py` e `upload_post_analytics_client.py` têm ramos que dependem de env vars e deps opcionais. A catraca sempre sai do log do CI. |
| 2026-08-12 | **R-22** | `post.job.output` envolvido em `try/except RenderOutput.DoesNotExist` (`tasks.py:2498`); teste caracterizador invertido de `assertRaises` para `FAILED`. Commit `ffcfc27`, PR #33. | **Item que não existia no plano.** O acesso ao OneToOne reverso levantava *antes* da guarda que trataria o caso — a guarda era código morto desde sempre. O efeito em produção é silencioso do jeito ruim: o post fica preso em PENDING e a task queima as 3 tentativas de retry sem registrar o motivo em lugar nenhum além do log do worker. |
| 2026-08-12 | **R-23** | `post.error` zerado no ramo de sucesso quando não há warnings (`tasks.py:3568`) + teste novo do ramo com warnings. Commit `2322efa`, PR #34. | **Item que não existia no plano.** `"error"` está no `update_fields`, então o texto da tentativa anterior era *regravado* junto com `status=DONE` — um post publicado com sucesso exibia o erro da tentativa que falhou, para sempre. O teste do ramo com warnings passa nas duas versões: é ele que prova que a limpeza não engoliu a ressalva legítima. |
| 2026-08-12 | **R-06** | `mark_posted` e `mark_still_scheduled` extraídas de `tasks.py` para `apps/social/services/posting_state.py`, cada uma em `transaction.atomic()`. 6 testes novos de rollback. Commit `1e85d53`, PR #35, mergeado em `995980d`. | Duas. (1) O R-03 **já tinha previsto a inversão**: `test_transition_is_not_atomic_today` dizia no docstring "R-06 deve INVERTER esta asserção" — o characterization test funcionou como contrato entre dois itens separados por dias. (2) `tasks.py` está commitado com **CRLF** neste repo enquanto todo o resto é LF; reescrever o arquivo por script normalizou para LF e inflou o diff de 84 para **8.014 linhas**. Corrigido com `git -c core.autocrlf=false add`. Quem mexer em `tasks.py` por script: conferir o `--stat` antes de commitar. |
| 2026-08-12 | **PRs #33–#34** | As duas correções mergeadas em `develop` (`7c316a4`). | Conflito no `refactor.md`: as duas branches saíram de `develop` e inseriram bloco de checklist no mesmo ponto. Código e testes juntaram limpo — as correções tocam `tasks.py:2498` e `:3568`. Em itens irmãos que atualizam o mesmo documento, contar com conflito no doc mesmo quando o código não conflita. |
| 2026-08-11 | **R-03** | 31 characterization tests das 5 cópias, passando contra o código atual sem alterá-lo. PR #31. | As 5 cópias concordam em **apenas 3 campos**. A divergência 1 (ramo não-YouTube não deduplica `PostedVideoLog` nem valida id vazio) é bug claro. Confirmou também que o diagnóstico D-02 subestimava o problema: não era só duplicação, era duplicação **com uma das cópias quebrada**. |

---

> **Para as próximas sessões:** ao concluir qualquer item deste plano, atualize este
> arquivo — marque as caixas, preencha o hash do commit, ajuste o contador de progresso
> no topo da seção 13 e anote no registro de execução acima. Se a realidade divergir do
> plano, **corrija o plano**, não o abandone.

---

## 14. Fora do escopo / fora do foco

ESCOPO e FOCO vieram vazios (projeto global), então esta seção não se aplica. O que ficou
de fora por decisão técnica está na **seção 12**, com justificativa.

Merecem projeto próprio, depois deste:

- Refatoração do frontend React (`CortesAutomaticos.jsx` 2.387 linhas, `api.js` 1.183, zero teste de componente).
- Unificação dos campos `status` em `TextChoices` — único item que exigiria migration.
- Introdução de type checking (mypy/pyright) — hoje inexistente, ver limitação L-3.

---

## 15. Limitações

**L-1 · ~~Cobertura real desconhecida~~ — RESOLVIDO em R-02.** A cobertura real do código de
produção é **40,81%** (12.844 statements, 7.061 sem cobertura). O portão anterior declarava
70% medindo 7,5% do código. As metas da seção 10 agora têm base medida.

**L-2 · ~~Suíte local com 7 erros~~ — RESOLVIDO em R-01.** Dos 7 erros, 6 eram o bug de D-06
e 1 era `prometheus_client` faltando no `.venv` local (dependência já declarada em
`requirements.txt`; o venv foi sincronizado). Hoje: **294 testes, 60s, `OK`**.

**L-3 · Sem type checking.** Não há mypy nem pyright. Não pude usar análise de tipos para
mapear dependências, o que significa que a contagem de 96 imports dentro de função é o
melhor proxy que tenho para ciclos — mas não é um grafo de dependência de verdade.

**L-4 · Duplicação medida por padrão, não por ferramenta.** Não rodei detector de código
duplicado. A duplicação do D-02 foi encontrada por `grep` de padrão de estado e é sólida
(5 sites concretos, com arquivo e linha). Pode haver outras duplicações estruturais que
não aparecem em busca textual.

**L-5 · Não observei o sistema em produção.** Volume de publicações por hora, concorrência
real, tempo médio de lock no Postgres, tamanho da fila `publish` — nada disso foi medido.
A classificação de R-06 como "requer cuidado" (e não "transparente") é conservadora por
isso. **Quem conhece o tráfego real deve confirmar a janela de deploy.**

**L-6 · Estimativas de esforço.** São de um leitor de código, não de quem conhece o
histórico. Itens de movimentação pura (R-08, R-15, R-16, R-18) têm estimativa confiável.
Os de fatiamento de função crítica (R-09 a R-12) podem facilmente custar 50% mais se
aparecerem acoplamentos que a leitura estática não revelou. **A margem de erro está
concentrada na onda 1.**

**L-7 · ~~Decisões de produto pendentes~~ — RESOLVIDO em 2026-08-13.** As quatro decisões
que bloqueavam o R-07 foram tomadas com o código das 5 cópias na frente. Todas venceram
pela mesma lógica: onde uma cópia divergia por esquecimento, ganhou a maioria; onde
divergia por bom motivo, o bom motivo virou regra geral.

| # | Divergência | Decisão |
| --- | --- | --- |
| 1 | Só o ramo não-YouTube gerava log duplicado e log com id vazio | **Bug.** Todos deduplicam; nenhum grava log sem id externo |
| 2 | A marcação manual não zerava `schedule.next_retry_at` | **Sempre zerar** — schedule `DONE` com retry pendente é estado sujo |
| 3 | Marcação manual e comando de reparo não mexiam em `attempt_count` | **Sempre sincronizar** com `post.retry_count` |
| 4 | Só o comando de reparo preservava `posted_at`/`scheduled_for` | **Preservar virou a regra geral** |
| 5 | Só a cópia C levava o `ScheduledPost` a `DONE` | **Sempre**, com `save()` condicional |

Sobre a **4**: no fluxo normal esses campos estão vazios quando a transição roda, então
nada muda ali — a regra existe para o caso de reparo. A única exceção é a data explícita
digitada na API, que é afirmação deliberada do operador e sobrescreve.

Aplicadas no R-07 ([#36](https://github.com/Cascapera/social_automation/pull/36)) e
travadas em teste. **Continua em aberto** a quinta pergunta, que é de processo e não de
código:

- **Vale congelar features em `apps/social/` durante a onda 1** (~2 semanas), ou é
  preferível aceitar rebases frequentes? Isso muda o risco de conflito de "alto" para
  "baixo".

**L-9 · Três afirmações deste documento estavam erradas sobre a mesma constante.**
`YOUTUBE_CHECK_CLIENT_ENABLED` aparecia como "área intestável" (seção 6), como
`LIMITE CONHECIDO` do R-04 e como a prioridade do R-17 — todas dizendo que existia um
ramo intestável porque a flag era lida no import. **Era código morto: ninguém a lia.**
Removida no R-17 lote 1.

O erro tem causa comum e vale para o resto do plano: a análise leu a *definição* da
constante e inferiu o *uso* pelo nome dela, sem verificar se havia leitor. É o mesmo
formato de erro do **L-4** (duplicação medida por padrão textual, não por ferramenta).

**A dúvida se confirmou no mesmo dia.** O R-18 encontrou a segunda ocorrência:
`CTR_WORDS_PT`, `CTR_WORDS_EN`, `FORBIDDEN_WORDS_PT` e `FORBIDDEN_WORDS_EN` — ~60 linhas
de listas de palavras em `grok.py`, descritas no D-09 como parte do conteúdo de prompt —
**também não são lidas por nenhum código**. Foram movidas para `prompts/vocabulary.py` em
vez de apagadas: são conteúdo editorial e a intenção de quem as escreveu não está no
código, diferente do booleano do R-17.

> ▶ **Decisão pendente para você:** ligar `CTR_WORDS_*` / `FORBIDDEN_WORDS_*` ao pipeline
> (elas parecem ter sido escritas para filtrar títulos e thumbnails) ou removê-las? Não
> bloqueia nada; enquanto não sair, ficam declaradas e sinalizadas no docstring.

O R-19 (c) achou mais duas, e uma delas é pior que constante morta: **`horizontal_insert_logo`
é um parâmetro que atravessa a fila.** A `apps/api/views.py` lê o valor do request e o envia
na mensagem Celery; o corpo da task **nunca o consultou**. Quem mexer no checkbox da tela não
vai ver efeito nenhum, e o log não vai dizer por quê. Ficou na assinatura — removê-lo
quebraria as mensagens já enfileiradas — mas agora está escrito no docstring. Junto veio
`DEFAULT_SUBTITLE_STYLE`, o alias que sobrou sem leitor quando os dois estilos viraram
`_SHORT` e `_LONG`.

Quatro ocorrências em quatro itens sugerem que a pergunta vale para o resto: das 57 ocorrências
de `os.getenv` do D-08 e das contagens de constantes deste documento, **quantas alimentam
algo que alguém lê?** Vale rodar a verificação antes de estimar o próximo item, não depois.

> ▶ **Decisão pendente para você:** o checkbox de "inserir logo" na finalização deveria
> funcionar (e aí é bug de produção, não código morto) ou sair da tela?

**L-10 · Um teste de rede pode ter escopo largo demais, e isso custa.** O congelamento de
hash do R-18 pegava *toda* constante em maiúsculas de `grok.py`. No dia seguinte, um commit
que só adicionava o preço do `gemini-2.5-flash` **deixou o `develop` vermelho** — sem
nenhum ganho, porque preço não é prompt.

A causa: a mesma lista foi usada para dois propósitos com escopos diferentes. Para **provar
que o R-18 foi movimentação pura**, varrer tudo era o certo — qualquer constante que mudasse
indicaria recorte errado. Para **travar conteúdo que vai ao modelo**, tinha de ser só
prompt. Corrigido em [#39](https://github.com/Cascapera/social_automation/pull/39): saíram
as 7 entradas de preço, alias de modelo, base URL de provedor e rótulo de métrica; ficaram
as 24 de conteúdo, com a fronteira presa a `prompts.__all__` e verificada nos dois sentidos.

**A lição para os próximos itens:** ao converter uma verificação de refatoração em teste
permanente, perguntar o que ela vai cobrar de quem mexer no código depois. Rede que cobra
cerimônia de mudança legítima acaba desativada — ou pior, tolerada vermelha. Vale para os
anti-drift que já estão no repositório (`posting_state`, `YOUTUBE_CHECK_*`): os dois são
estreitos de propósito, mas convém reler cada um com esta pergunta antes de somar o próximo.

**L-11 · Fatiar a função revelou duas duplicações que estavam escondidas pelo tamanho.**
Ao separar `analyze_auto_cuts_task` (R-19 b), duas constantes/funções apareceram em dois
lugares ao mesmo tempo:

| O quê | Onde | Diferença entre as cópias |
| --- | --- | --- |
| `ALL_THEME_CATEGORIES` (5 códigos) | `prompts/vocabulary.py` e `services/analysis_flow.py` | nenhuma — listas idênticas |
| `_mark_analysis_done` | `services/flow_common.py` e `services/recovery.py:260` | a de `recovery` salva direto, sem a guarda de linha apagada; o log tem prefixo `[RECOVERY]` |

Nenhuma das duas é bug hoje. As duas são o mesmo padrão do **D-02** em escala menor: o
estado "análise concluída" tem dois donos, e quem mudar um lado não vê o outro. A de
`ALL_THEME_CATEGORIES` tem um detalhe: a cópia de `prompts/` está congelada por hash
(R-18), então unificar exige atualizar o hash no mesmo PR — **é PR próprio, não carona**.

Ficam registradas aqui em vez de corrigidas junto: o R-19 (b) é movimentação pura por
contrato, e unificar as duas mudaria comportamento no caminho da `recovery`.

**L-12 · A mesma variável de ambiente com dois defaults diferentes.** No R-17 lote 2,
`WHISPER_MODEL` era lida em 5 lugares — três com default `small`, dois com `large-v3`.
Ninguém decidiu isso: cada leitor escolheu o seu quando precisou, e a diferença nunca
apareceu numa revisão porque cada ocorrência isolada parece razoável.

O detalhe que importa: **com a variável definida no ambiente, os dois caminhos concordam.**
A divergência só aparece quando ela está ausente — que é o caso de qualquer máquina que
não tenha o `.env` completo. Ou seja, o comportamento muda entre ambientes por omissão,
não por configuração.

A migração preservou os dois defaults, em duas settings sobre a mesma env var. A
alternativa — uma setting só — teria efeito silencioso em produção: vídeo longo passaria a
carregar um modelo 10× maior em cada bloco, ou o curto perderia qualidade. **Refatoração
não é o momento de escolher entre `small` e `large-v3`**; o momento é uma decisão de
qualidade × custo, com o número do tempo de transcrição na mão.

A lição vale para o lote 3: antes de unificar uma leitura repetida, comparar os defaults
de todas as ocorrências. Se divergirem, a unificação vira mudança de comportamento
disfarçada de limpeza.

**L-8 · Não executei nada além da suíte de testes.** Não subi a aplicação, não processei
vídeo, não chamei a API do YouTube. Toda afirmação sobre comportamento em runtime vem de
leitura de código e da execução dos testes.
