# Feature — Seleção de cortes: duração longa, sinais do autor e nota honesta

```
Status: **concluído** · Progresso: 9/9 PRs · 14/14 itens · Suposições: 5 · Questões em aberto: 1 · Suposições: 5 · Questões em aberto: 1 · 2026-08-24
```

## 1. Resumo

Três mudanças no que o LLM recebe e uma no que o backend faz com a resposta.
(1) Cortes longos passam da faixa 8–15 min para **8–40 min**, em todos os sete modos.
(2) Os prompts passam a tratar **sinalização explícita de quem fala** ("presta atenção
nessa parte", "isso aqui daria um bom corte") como indício forte de corte bom, com a
direção certa quando a dica aponta para trás. (3) A nota passa a ser **calibrada e
honesta**, com faixas explícitas e permissão para pontuar baixo em conteúdo fraco — o que
só vira ganho real se vier junto com pedir candidatos **com margem** e um **filtro de nota
mínima** no backend, ambos incluídos aqui.

No outro extremo da duração, o **teto de short vira regra única** em vez de três ramos
`if/elif` com um `else` sem limite, e o modo educacional cai de **180s para 150s** — 180
tangencia o limite de 3 min do YouTube Shorts e, em produção, às vezes o ultrapassa depois
do re-encode, fazendo o vídeo ser tratado como vídeo longo.

## 2. Contexto do código

### O que já existia e foi aproveitado

| Documento | Como foi usado |
| --- | --- |
| `docs/LLM_AUDIT.md` (24/05/2026) | Snapshot, avisa no topo que não descreve o código de hoje. Conferido item a item: o que lista como implementado está no código. Os três alertas abertos (retry sem fallback de modelo, `analyze_ready_cuts_batch_titles_from_job_name` órfã, sem cache de resposta) **não são tocados por esta feature**. |
| `docs/AUTO_CUTS_STRATEGY.md` | Doc de domínio dos prompts, **desatualizado**: diz shorts 15–90s / máx. 3 min, longos 10–30 min e score 1–10 para todos. Os 8–40 min desta feature estão mais perto dele do que os 8–15 do código atual. Atualizar faz parte do escopo (F-02, F-11). |
| `refactor.md` R-18 / D-09 | Explica a separação `prompts/` × `services/grok.py` e a trava de hash. É a convenção que esta feature segue. |

### Onde a feature encaixa

| Camada | Arquivo | O que muda |
| --- | --- | --- |
| Vocabulário de prompt | `apps/auto_cuts/prompts/vocabulary.py` | dois blocos de regra novos |
| Papel do modelo | `apps/auto_cuts/prompts/system.py` | 7 prompts: duração, blocos novos, escala de nota |
| Mensagem do usuário | `apps/auto_cuts/prompts/templates.py` | 7 templates: duração, `author_cue`, exemplos |
| Montagem da requisição | `apps/auto_cuts/services/grok.py:583-700` | quantidade pedida com margem |
| Consumo da resposta | `apps/auto_cuts/services/analysis_flow.py:772-1001` | teto dos longos, ordem dos filtros, filtro de nota |
| Render de vídeo | `apps/jobs/services/ffmpeg.py:140-193` | `overlay_animation` com duração de saída determinística |
| Trava de conteúdo | `apps/auto_cuts/tests/test_grok_prompts_integridade.py` | hash de cada constante alterada |
| Configuração | `social_automation/settings.py`, `.env.example` | margem e score mínimo |

### Convenções que esta feature segue

- **Prompt é conteúdo, mora em `prompts/`.** Bloco de regra reaproveitado por vários
  prompts vira constante em `vocabulary.py` e é concatenado nos system prompts — é como
  `ANTI_AUTOMATION_RULES_PT` e `METADATA_SAFETY_RULES_PT` já funcionam.
- **Toda constante exportada em `prompts.__all__` entra no congelamento de hash.**
  `CoberturaDoCongelamentoTests` verifica as duas listas uma contra a outra: bloco novo sem
  hash novo quebra o build. Isso é o mecanismo funcionando, não um obstáculo — cada PR que
  mexe em prompt atualiza o hash no mesmo PR e descreve a mudança editorial no commit.
- Limite numérico é constante nomeada no topo de `analysis_flow.py`.
- Log com prefixo `[FLUXO]`; mensagem de usuário em `analysis.progress_message`.
- Teste em `apps/<app>/tests/test_*.py`, sob `social_automation.settings_test`.

### Estado da área alvo

`_create_suggestions` (`analysis_flow.py:772-1001`) concentra o clamp de duração, a
ordenação por nota e o ponto onde o filtro novo entra — e **não tem teste nenhum hoje**.
`test_analyze_task_characterization.py` trava só as fronteiras da task (desistência
silenciosa, `status="error"` com mensagem literal, reagendamento, delegação) e diz isso no
docstring: o miolo ficou de fora de propósito. `test_grok_prompts_integridade.py` congela o
texto dos prompts, mas não afirma nada sobre comportamento. Daí o **PR 0**: caracterizar
antes de mexer.

Nada duplicado: não existe filtro por nota em lugar nenhum do repositório, nem regra de
sinais do autor em nenhum prompt. É adição, não extensão.

## 3. Escopo

### Entra

- Faixa de duração dos cortes longos: 8–40 min, valendo para os 7 modos.
- `duration_minutes` calculado sempre a partir dos timestamps reais.
- Bloco de regra "sinais do autor" nos 7 prompts (PT e EN).
- Campo `author_cue` no JSON de cada clipe, preenchido só quando houver sinalização.
- Bloco de calibração de nota nos 7 prompts, com faixas e permissão de nota baixa.
- Escala única 0–100 de `virality_score`, incluindo os dois modos educacionais.
- Quantidade de candidatos pedida ao LLM proporcional ao alvo do job, com margem de 1,5×.
- Reordenação dos filtros de seleção (nota → roteamento → corte na quantidade).
- Filtro de score mínimo configurável, desligado por default.
- **Teto duro de duração para shorts**, aplicado a todo short depois dos ramos por modo.
- **Modo educacional: shorts de 120–150s** (hoje 120–180s), no prompt e no código.
- `-shortest` no `overlay_animation`, o único ponto do pipeline onde a duração de saída não
  é limitada pela duração de entrada.
- Atualização de `docs/AUTO_CUTS_STRATEGY.md`.

### Não entra

- **Alinhar o fim do corte ao fim de segmento** quando o teto é atingido (hoje o corte é
  truncado no meio da frase). Muda a fronteira de todo corte, em todos os modos: merece
  decisão própria, com amostra antes e depois.
- Remoção do overlap de 3 min no chunking (−13% de input).
- Troca de provider/modelo e correção da métrica que rotula pelo modelo pedido em vez do
  modelo usado.
- Honrar `shorts_target` acima de 10 na entrega — o teto de 10 continua; muda só quanto se
  **pede** ao LLM. Ver **Q-02**.
- Migração de score de análises educacionais antigas (decidido: não migrar).
- Mudança na escala do prompt de corte pronto (`READY_CUT_SYSTEM_PROMPT_BASE`, 1–10), que
  não passa por `_create_suggestions` e já é convertido explicitamente em
  `analysis_flow.py:1105`.
- Qualquer alteração de frontend.

## 4. Regras de negócio

| ID | Regra |
| --- | --- |
| **RN-01** | Corte longo com duração real abaixo de **8 min** é descartado; acima de **40 min** é truncado em 40. Vale para os 7 modos. |
| **RN-02** | `duration_minutes` é sempre derivado dos timestamps do corte, nunca do `duration_min` devolvido pelo LLM. |
| **RN-03** | O prompt instrui o modelo a tratar sinalização explícita de quem fala como indício forte de corte bom. |
| **RN-04** | Dica que aponta para frente → o corte começa na dica (ou poucos segundos antes) e vai até a conclusão do raciocínio. Dica que aponta para trás → o corte começa antes da dica, no início da explicação que ela resume. |
| **RN-05** | A frase-marcador nunca aparece em `suggested_title`, `thumbnail_text` nem `hook_sentence`. |
| **RN-06** | Havendo sinalização, o clipe traz `author_cue` com a frase do autor, no máximo 120 caracteres, como aparece na transcrição. Não havendo, `author_cue` é `""`. |
| **RN-07** | `virality_score` é 0–100 em **todos** os modos, incluindo educacional. |
| **RN-08** | O prompt define as faixas 85–100 / 70–84 / 50–69 / 0–49, declara esperado que parte dos candidatos fique abaixo de 50 e proíbe inflar nota para preencher a quantidade pedida. |
| **RN-09** | A quantidade de candidatos pedida ao LLM é `ceil(alvo_do_job × LLM_CANDIDATE_MARGIN)`, limitada pelos tetos `LLM_MAX_SHORTS` / `LLM_MAX_LONGS`. |
| **RN-10** | O mínimo exigido na validação da resposta é calculado sobre a quantidade **pedida**, não sobre o valor de env. |
| **RN-11** | A seleção final aplica, nesta ordem: ordenação por nota → descarte por score mínimo → descarte por roteamento de factory → corte na quantidade alvo. |
| **RN-12** | Corte com `virality_score` abaixo de `AUTO_CUT_MIN_VIRALITY_SCORE` é descartado. Default `0` = filtro desligado, comportamento idêntico ao de hoje. |
| **RN-13** | Item sem nota (`virality_score` ausente ou não numérico) **não** é descartado pelo filtro: é tratado como não avaliado e segue para as etapas seguintes. |
| **RN-14** | Se o filtro zerar os cortes, a análise **não** vira erro: segue o fluxo, registra em log quantos caíram e por qual nota, e grava `progress_message` explicando. |
| **RN-15** | A entrega continua limitada a 10 shorts e 5 longos por job, independentemente do alvo configurado. |
| **RN-16** | Nenhum short pode ser criado com duração acima de `SHORT_MAX_SEC_HARD` (**170s**), qualquer que seja o `prompt_version` — inclusive um modo novo que não tenha faixa própria definida. O teto é aplicado **depois** dos ramos por modo, sobre todo short. |
| **RN-17** | No modo educacional (PT e EN), a faixa de short passa a ser **120–150s**, no prompt e no código. Corte acima de 150s é truncado em 150s. |
| **RN-18** | As faixas por modo continuam valendo e são todas menores que o teto duro: viral 30–60s, viral_long 80–160s, educacional 120–150s. O teto duro é rede de segurança, não a regra do dia a dia. |
| **RN-19** | `overlay_animation` produz vídeo com a mesma duração do vídeo de entrada, independentemente da duração ou do conteúdo do asset de overlay. |

## 5. Critérios de aceite

| ID | Dado / Quando / Então | Regras |
| --- | --- | --- |
| **CA-01** | Dado um long de 42 min devolvido pelo LLM em qualquer modo · quando as sugestões são criadas · então o corte fica com exatamente 40 min e `end_tc` é recalculado | RN-01 |
| **CA-02** | Dado um long de 6 min · quando as sugestões são criadas · então ele é descartado e o descarte aparece no log | RN-01 |
| **CA-03** | Dado um long educacional com `duration_min: 90` no JSON e timestamps de 22 min · quando a sugestão é criada · então `duration_minutes` é 22,0 | RN-02 |
| **CA-04** | Dado qualquer um dos 7 system prompts · quando é montado · então contém o bloco de sinais do autor no idioma correspondente | RN-03, RN-04, RN-05 |
| **CA-05** | Dado um clipe com `author_cue` no JSON do LLM · quando a sugestão é criada · então o valor está preservado em `raw_data["author_cue"]` | RN-06 |
| **CA-06** | Dado os dois prompts educacionais · quando são montados · então pedem `virality_score` 0–100 e nenhum exemplo usa a escala 1–10 | RN-07 |
| **CA-07** | Dado qualquer um dos 7 system prompts · quando é montado · então contém as quatro faixas de nota e a frase que autoriza nota abaixo de 50 | RN-08 |
| **CA-08** | Dado um job com `shorts_target=4` e `longs_target=2` · quando a requisição é montada · então pede 6 shorts e 3 longos | RN-09 |
| **CA-09** | Dado um job com `shorts_target=12` e teto `LLM_MAX_SHORTS=15` · quando a requisição é montada · então pede 15 (o teto), não 18 | RN-09 |
| **CA-10** | Dado uma resposta com 6 candidatos para um pedido de 6 · quando é validada · então passa (mínimo calculado sobre o pedido, não sobre o env) | RN-10 |
| **CA-11** | Dado 12 candidatos ordenados, sendo os 3 primeiros sem mapeamento de factory · quando a seleção roda com alvo 10 · então os 3 saem antes do corte de quantidade e a entrega usa os seguintes da fila | RN-11 |
| **CA-12** | Dado `AUTO_CUT_MIN_VIRALITY_SCORE=70` e candidatos com notas 90, 71, 70, 69 · quando a seleção roda · então entrega 3 cortes e descarta o de 69 | RN-12 |
| **CA-13** | Dado `AUTO_CUT_MIN_VIRALITY_SCORE=0` · quando a seleção roda · então nenhum corte é descartado por nota | RN-12 |
| **CA-14** | Dado um candidato com `virality_score` ausente e limiar 70 · quando a seleção roda · então ele **não** é descartado pelo filtro de nota | RN-13 |
| **CA-15** | Dado limiar 95 e todos os candidatos abaixo · quando a seleção roda · então a análise segue sem `status="error"`, com `progress_message` explicando e log do total descartado | RN-14 |
| **CA-16** | Dado um `prompt_version` que não cai em nenhum ramo por modo e um short de 400s · quando a sugestão é criada · então ela nasce com 170s e `end_tc` recalculado | RN-16 |
| **CA-17** | Dado cada um dos 7 modos, percorridos a partir de `AutoCutAnalysis.prompt_version.choices` · quando um short absurdamente longo é devolvido · então **nenhum** modo cria short acima de 170s | RN-16, RN-18 |
| **CA-18** | Dado um short educacional de 175s · quando a sugestão é criada · então ela fica com 150s | RN-17 |
| **CA-19** | Dado os dois prompts educacionais · quando são montados · então pedem 120–150s e nenhum texto menciona 180 seg nem "2–3 min" | RN-17 |
| **CA-20** | Dado um vídeo de 60s e um asset de overlay de 8s · quando o overlay é aplicado · então a saída tem 60s (±1 frame) | RN-19 |

## 6. Contrato

**Nenhum endpoint novo, nenhuma rota alterada.** A feature muda o conteúdo enviado ao LLM e
a lógica interna de seleção.

Uma única mudança observável na API: `AutoCutSuggestion.raw_data` — já serializado como
JSON opaco em `apps/api/serializers/auto_cuts.py` — passa a poder conter a chave
`author_cue`. É adição de chave em campo livre, sem breaking change para quem consome.

Contrato pedido ao LLM (trecho novo em cada clipe):

```json
{
  "clip_number": 1,
  "start_timestamp": "12:04",
  "end_timestamp": "12:58",
  "virality_score": 88,
  "author_cue": "presta atenção nessa parte porque é o que ninguém entende"
}
```

`author_cue` é `""` quando não houver sinalização. Ausência da chave é tratada como `""` —
a resposta do LLM nunca é confiada como completa.

## 7. Modelo de dados e migração

**Nenhuma migração.** Nenhum campo novo, nenhum índice, nenhuma tabela.

- `author_cue` entra em `AutoCutSuggestion.raw_data`, que é `JSONField` e já recebe o item
  inteiro do LLM (`raw_data=item`) — não precisa de coluna.
- O score mínimo é configuração de ambiente, não coluna.
- Análises educacionais antigas mantêm score na escala 1–10 (decidido: não migrar). O score
  só é usado para ordenar e filtrar **no momento em que a sugestão é criada**; linha antiga
  não é reordenada nem refiltrada. Fica registrado aqui e no `AUTO_CUTS_STRATEGY.md` que
  registros anteriores a este projeto, em modo educacional, usam a escala antiga.

Configurações novas em `social_automation/settings.py`:

| Variável | Default | Efeito |
| --- | --- | --- |
| `LLM_CANDIDATE_MARGIN` | `1.5` | Multiplicador do alvo do job na quantidade pedida ao LLM |
| `AUTO_CUT_MIN_VIRALITY_SCORE` | `0` | Nota mínima para o corte ser criado. `0` = desligado |

E dois já existentes que passam a ser **teto**, não quantidade fixa: `LLM_MAX_SHORTS`
(hoje `10`) e `LLM_MAX_LONGS` (hoje `5`). Sem alterar o `.env` de produção, o
comportamento fica igual ao de hoje para job de alvo 10 — ver rollout.

## 8. Decisões de design

```
DECISÃO D-01: onde mora o teto de duração dos cortes longos
Opção A — manter o clamp dentro do `if is_viral_prompt`, mudando só o valor
  · a favor: diff mínimo, não toca o caminho educacional
  · contra: educacional continua sem teto nenhum (usa `duration_min` cru do LLM),
    e é exatamente esse padrão de if/elif que deixou o clamp de shorts com um `else`
    sem limite — repetir o padrão é repetir o furo
Opção B — constante única aplicada a todo corte longo, seja qual for o modo
  · a favor: uma regra, um lugar, sem ramo esquecido; modo novo nasce coberto
  · contra: muda o comportamento do educacional (que hoje aceita qualquer duração)
RECOMENDAÇÃO: B. O modo educacional já pede 20–40 min no prompt; aplicar um teto de 40
  não contraria a intenção editorial, só impede que uma resposta fora da faixa passe.
TRADE-OFF ACEITO: um long educacional de 50 min que hoje passaria inteiro passa a ser
  truncado em 40.
```

```
DECISÃO D-02: como cobrir o que o filtro descarta — pedir mais ou entregar mais
Opção A — margem no pedido ao LLM, entrega continua limitada a 10
  · a favor: o caro deste pipeline é ffmpeg (extração, legenda queimada, thumbnail,
    finalização), não token. Margem custa output de LLM e nada de vídeo
  · contra: não resolve o teto silencioso de 10 na entrega (Q-02)
Opção B — honrar `shorts_target` até 30 na entrega
  · a favor: o campo passa a significar o que diz
  · contra: multiplica o processamento de vídeo por até 3× por job
RECOMENDAÇÃO: A. Resolve o problema desta feature (o filtro precisa de folga) pelo lado
  barato. B é decisão própria, com número de custo de ffmpeg na mão.
TRADE-OFF ACEITO: `shorts_target` acima de 10 continua sem efeito na entrega.
```

```
DECISÃO D-03: ordem entre corte de quantidade e filtros
Opção A — manter como está: ordena, corta em N, depois filtra roteamento
  · a favor: nenhum diff
  · contra: candidato bom na posição 11 nunca substitui um descartado na posição 3 —
    é o que faz o job entregar menos que o alvo mesmo tendo candidato válido de sobra,
    e anularia a margem do D-02
Opção B — ordena, filtra nota, filtra roteamento, e só então corta em N
  · a favor: a margem passa a valer; entrega o alvo sempre que houver candidato válido
  · contra: muda a composição do resultado mesmo com o filtro de nota desligado
RECOMENDAÇÃO: B, junto com a margem, no mesmo PR — separados, cada um sozinho não entrega
  o efeito.
TRADE-OFF ACEITO: um job pode passar a entregar cortes que hoje não entregaria; é o
  objetivo, mas é mudança de comportamento observável sem flag.
```

```
DECISÃO D-04: onde configurar o score mínimo  (confirmada em 24/08)
Opção A — env global, default 0
  · a favor: sem migração, sem serializer, sem tela; liga em produção quando quiser e
    calibra o número olhando o log de quantos caíram
  · contra: mesmo limiar para todas as factories
Opção B — campo por factory ou por job
  · a favor: controle fino
  · contra: escolher o número antes de ter medição de como as notas se distribuem
RECOMENDAÇÃO: A. Campo por factory vira decisão própria depois, com dado real.
TRADE-OFF ACEITO: quem tiver conteúdo de qualidade muito desigual entre factories vai
  querer o campo antes do que o plano prevê.
```

```
DECISÃO D-05: `author_cue` como campo do JSON  (confirmada em 24/08)
Opção A — só a regra no prompt
  · a favor: zero token extra
  · contra: sem evidência — não dá para saber se o modelo usou as dicas ou ignorou
Opção B — campo curto, preenchido só quando houver dica
  · a favor: ~10–15 tokens por clipe e a regra vira mensurável; vai para `raw_data`,
    sem migração
  · contra: mais uma chave que o modelo pode preencher errado
RECOMENDAÇÃO: B, sem usar na ordenação por enquanto — primeiro medir, depois decidir se
  vira peso de ranking.
TRADE-OFF ACEITO: custo de output ligeiramente maior em troca de poder medir.
```

```
DECISÃO D-06: qual o valor do teto duro de short
Contexto: o Shorts do YouTube classifica até 3 min (180s). Um corte de 180,0s vira 180,0x
  depois do re-encode a 30fps e do container, e sai da classificação — observado em
  produção ("às vezes passa e o YouTube trata como vídeo longo"). Faixas por modo hoje:
  viral 30–60s, viral_long 80–160s, educacional 120–180s.
Opção A — teto duro 170s, educacional cai para 150s
  · a favor: nenhum modo existente além do educacional muda de comportamento (o maior é
    viral_long a 160s); o teto pega só o buraco do `else` e modo novo, com 10s de folga
  · contra: viral_long segue com 20s de margem, menos folga que o educacional novo
Opção B — teto duro 150s para todos
  · a favor: margem uniforme de 30s
  · contra: trunca o viral_long, cuja faixa 90–160s é decisão editorial deliberada e está
    em produção sem o problema relatado
Opção C — teto duro 180s
  · a favor: é o limite oficial
  · contra: não resolve nada — é exatamente o valor que está estourando hoje
RECOMENDAÇÃO: A. O teto duro é rede de segurança para o que não tem faixa própria; quem
  tem faixa própria continua governado por ela.
TRADE-OFF ACEITO: viral_long fica com 20s de folga, e não 30s. Se o problema aparecer
  também lá, baixar a faixa dele é uma constante — mas hoje não há relato.
```

## 9. Impacto e compatibilidade

- **Sem breaking change de contrato.** Nenhuma rota, nenhum campo removido ou renomeado na
  API. `raw_data` ganha uma chave opcional.
- **Sem migração e sem janela.** Nada trava tabela, nada exige parada.
- **Mudança de comportamento observável, sem flag:** cortes longos podem passar de 15 min
  (PR 1) e a composição da lista de cortes muda (PR 5). Prompt não tem feature flag neste
  repositório e criar uma só para isso significaria manter dois textos vivos em paralelo —
  o mecanismo de reversão aqui é o revert do PR, que é barato porque cada PR é pequeno.
- **Quem consome hoje:** o frontend lê `duration_minutes` e `virality_score` para exibição;
  `factory_scheduler` lê `raw_data["tags"]`; `youtube_description` lê `tags` e `chapters`.
  Nenhum deles depende de faixa de duração nem de escala de nota — verificado.
- **Convivência de escalas:** sugestões educacionais criadas antes do PR 3 continuam com
  nota 1–10 no banco. Só afeta comparação visual entre registros antigos e novos.

## 10. Riscos e atenção

| # | Risco | Mitigação |
| --- | --- | --- |
| **R-1** | **Custo de processamento**: um long de 40 min re-encoda ~2,7× mais que um de 15 min em toda a cadeia (extração, canvas, legenda queimada, overlay). Fila e disco sobem na mesma proporção. | Observar a duração média dos longos e o tempo de finalização nos primeiros jobs depois do PR 1. Se doer, o teto é uma constante — baixar é uma linha. |
| **R-2** | **Direitos**: um long de 40 min sobre um vídeo de 45 min é, na prática, reupload do episódio inteiro. A política de direitos do projeto hoje é só idade mínima do vídeo (24h). | Observação de negócio, não bloqueio técnico. Se virar regra, o lugar é um teto relativo à duração da fonte — fora do escopo deste projeto. |
| **R-3** | **Estoque da factory**: nota honesta + filtro ligado forte pode secar o volume de cortes. | Default `0` (desligado). Subir gradual olhando o log de quantos caem por job. |
| **R-4** | **Mudança de prompt não é determinística**: o hash prova que o texto mudou, não que a saída melhorou. | Medir em N jobs antes/depois: % de clipes com `author_cue` preenchido, distribuição das notas, quantos cortes sobrevivem aos filtros. É para isso que o `author_cue` existe. |
| **R-5** | **Custo de token**: `author_cue` soma ~10–15 tokens por clipe; a margem de 1,5× aumenta o output em job de alvo alto e **reduz** em job de alvo baixo (hoje pede 10 fixo mesmo para alvo 3). | Efeito líquido depende da mistura de jobs. O contador de custo por modelo já existe em Prometheus. |
| **R-6** | **Segurança / dado pessoal**: `author_cue` é transcrição de fala do próprio vídeo — mesmo tratamento do `transcript` que já é armazenado. Não é entrada de usuário. | **`author_cue` não pode ser publicado**: não entra em título, descrição, tags nem primeiro comentário, e por isso não passa pelo `metadata_sanitizer`. Se algum dia for exibido ao público, precisa entrar no sanitizer antes. |
| **R-7** | **Concorrência / idempotência**: `_create_suggestions` apaga e recria as sugestões da análise. A feature não muda isso. | Nada a fazer; registrado para não ser reintroduzido como novidade. |
| **R-8** | **Corte educacional 17% mais curto**: uma explicação que hoje cabe em 170s passa a ser truncada em 150s. Como o prompt também passa a pedir 120–150s, o modelo tende a escolher blocos que fecham nessa janela em vez de blocos truncados — mas a janela ficou menor. | Se aparecer explicação cortada no meio com frequência, o caminho é o alinhamento ao fim de segmento (fora de escopo, ver "não entra"), não voltar para 180s. |
| **R-9** | **`-shortest` muda o comportamento de assets de overlay maiores que o clipe**: hoje o loop infinito com seleção automática de streams pode estender a saída; depois, a saída termina com o vídeo base. | É o comportamento desejado (RN-19). Verificar visualmente uma finalização com overlay animado depois do PR 8. |

## 11. Padrão de qualidade (adaptado a este repositório)

O repositório **não usa mypy** (sem dependência, sem config, sem passo no CI) e o CI **não
roda `ruff format --check`** — só `ruff check`, com `E501` ignorado. O critério de pronto
abaixo é o que se consegue verificar de verdade aqui; caixa que ninguém consegue marcar não
entra no checklist.

| Exigência | Regra |
| --- | --- |
| **Cobertura** | 100% do código novo desta feature, linha **e** branch, medido no `term-missing` dos arquivos tocados. A catraca global `--cov-fail-under=45.0` continua valendo e não pode cair. |
| **Lint** | `python -m ruff check .` sem violação. |
| **Django** | `python manage.py check --fail-level WARNING` limpo. |
| **Testes** | `python -m pytest -q` inteiro verde. Zero `skip`/`xfail` no que for escrito agora. |

- Cobertura vazia não conta: cada caminho tem asserção sobre comportamento (valor, exceção,
  linha gravada), não só execução.
- Branch coverage também — cada `if`, `except` e saída antecipada com caso passando por ele.
- Nada de consertar código existente de carona: achado fora do escopo vira observação, não
  commit.
- Item que não fecha os quatro critérios não é marcado como pronto. Reporte o vermelho com a
  saída colada.

## 12. Plano de implementação

Nove PRs, todos pequenos, cada um implantável sozinho.

### PR 0 — Caracterizar `_create_suggestions`

Trava o comportamento **atual** antes de qualquer mudança: clamp de 60s (viral), 160s
(viral_long) e 180s (educacional); descarte de short abaixo do mínimo; descarte de long
abaixo de 8 min; truncamento de long acima de **15 min** (o valor de hoje); ordenação por
nota e o composite do viral_long. Nenhuma mudança de comportamento.

> A asserção dos 15 min muda no PR 1. Essa mudança de asserção **é** a prova de que a regra
> mudou de propósito — é o mesmo mecanismo do hash de prompt, aplicado a comportamento.

- Arquivos: `apps/auto_cuts/tests/test_create_suggestions_characterization.py` (novo)
- Risco: nenhum · ~220 linhas · produção: transparente · reversão: deletar o arquivo

### PR 1 — Cortes longos de 8 a 40 minutos

- `analysis_flow.py`: `VIRAL_LONG_MIN_SEC`/`VIRAL_LONG_MAX_SEC` viram `LONG_CUT_MIN_SEC`/
  `LONG_CUT_MAX_SEC` (8 e 40 min), aplicados a **todo** corte longo (D-01); `duration_minutes`
  sempre calculado dos timestamps (RN-02).
- `system.py`: 4 pontos (`:45`, `:94` PT; `:183`, `:232` EN). Os dois educacionais mantêm o
  pedido de 20–40 min — faixa mais estreita dentro do teto, intenção editorial preservada.
- `templates.py`: 5 pontos (`:26`, `:146`, `:337`, `:457`, `:580`).
- Hashes dos prompts alterados; `docs/AUTO_CUTS_STRATEGY.md`.
- Risco: médio (R-1) · ~160 linhas · pré-requisito: PR 0 · reversão: revert

### PR 2 — Sinais do autor

- `vocabulary.py`: `AUTHOR_CUE_RULES_PT` / `AUTHOR_CUE_RULES_EN` (exemplos de fala, direção
  frente/trás, proibição de vazar o marcador, definição de `author_cue`).
- `system.py`: concatenar nos 7 prompts. `templates.py`: `author_cue` na lista de campos e
  nos exemplos de JSON dos 7 templates.
- Hashes; teste de que o bloco aparece nos 7 prompts e de que `author_cue` sobrevive em
  `raw_data`.
- Risco: baixo · ~280 linhas · pré-requisito: nenhum · reversão: revert

### PR 3 — Escala 0–100 no modo educacional

- `system.py:141` e o equivalente EN; `templates.py:290` e o exemplo EN.
- Hashes; teste de que nenhum prompt pede 1–10.
- Risco: baixo · ~60 linhas · pré-requisito: nenhum · **bloqueia o PR 6**

### PR 4 — Calibração honesta da nota

- `vocabulary.py`: `SCORE_CALIBRATION_RULES_PT` / `_EN` com as quatro faixas e a autorização
  explícita de nota abaixo de 50.
- `system.py`: concatenar nos 7 prompts. Hashes; teste de presença.
- Risco: baixo · ~140 linhas · pré-requisito: PR 3 (mesma escala) · reversão: revert

### PR 5 — Margem de candidatos e ordem dos filtros

- `grok.py`: `analyze_chunks_in_one_request` recebe `max_shorts`/`max_longs`; o mínimo da
  validação passa a ser calculado sobre o pedido (RN-10).
- `analysis_flow.py`: `_request_llm_analysis` calcula o pedido a partir de `shorts_target`/
  `longs_target`; `_create_suggestions` filtra antes de cortar na quantidade (D-03).
- `settings.py` + `.env.example`: `LLM_CANDIDATE_MARGIN`; `LLM_MAX_SHORTS`/`LLM_MAX_LONGS`
  documentados como teto.
- Risco: médio (muda composição do resultado) · ~200 linhas · pré-requisito: PR 0

### PR 6 — Filtro de score mínimo

- `settings.py` + `.env.example`: `AUTO_CUT_MIN_VIRALITY_SCORE`, default 0.
- `analysis_flow.py`: descarte por nota na ordem definida em RN-11, log do total descartado
  e `progress_message` quando zerar (RN-14). Item sem nota não é descartado (RN-13).
- `docs/AUTO_CUTS_STRATEGY.md`: como calibrar o número.
- Risco: baixo (default desligado) · ~150 linhas · pré-requisito: PR 3 e PR 5

### PR 7 — Teto duro de short e educacional em 150s

- `analysis_flow.py`: `SHORT_MAX_SEC_HARD = 170` aplicado a **todo** short depois dos ramos
  por modo — fecha o `else` que hoje passa sem limite nenhum (D-06, RN-16);
  `EDUCATIONAL_SHORT_MAX_SEC` de 180 para 150.
- `system.py`: 7 pontos de texto do educacional (`:121`, `:138`, `:140` PT; `:271`, `:272`,
  `:288`, `:290` EN) — "2–3 min (120–180 seg)" vira "2–2,5 min (120–150 seg)".
- `templates.py`: 4 pontos (`:263`, `:321` PT; `:699`, `:758` EN).
- Hashes; teste que percorre `AutoCutAnalysis.prompt_version.choices` e prova o teto em
  **todos** os modos, inclusive um `prompt_version` inexistente.
- Risco: baixo · ~180 linhas · pré-requisito: PR 0 · reversão: revert

### PR 8 — Duração de saída determinística no overlay

- `apps/jobs/services/ffmpeg.py`: `overlay_animation` ganha `-map 0:v -map 0:a? -shortest`.
  É o único ponto do pipeline em que a duração da saída não é limitada pela entrada: o asset
  entra com `-stream_loop -1` e, sem `-map` explícito, a seleção automática de streams pode
  escolher o áudio do asset — que nunca termina.
- Teste com asset sintético maior que o clipe, comparando a duração da saída.
- Risco: baixo · ~40 linhas · pré-requisito: nenhum · reversão: revert

## 13. Plano de testes

| Tipo | O que cobre | Onde |
| --- | --- | --- |
| Caracterização (DB) | comportamento atual de `_create_suggestions` antes da mudança | `test_create_suggestions_characterization.py` |
| Unitário (DB) | CA-01, CA-02, CA-03, CA-05, CA-11, CA-12, CA-13, CA-14, CA-15 | mesmo arquivo, ampliado por PR |
| Unitário (sem DB) | CA-04, CA-06, CA-07 — presença e ausência de texto nos 7 prompts | `test_grok_prompts_integridade.py` (arquivo já é `SimpleTestCase`) ou arquivo irmão |
| Unitário (mock de cliente) | CA-08, CA-09, CA-10 — quantidade pedida e validação do mínimo | `test_llm_provider.py` (já mocka o cliente OpenAI) |
| Unitário (DB, matriz de modos) | CA-16, CA-17, CA-18 — teto duro percorrendo todos os `prompt_version.choices` mais um valor inexistente | `test_create_suggestions_characterization.py` |
| Unitário (sem DB) | CA-19 — ausência de "180" e "2–3 min" nos prompts educacionais | junto de CA-04/CA-06/CA-07 |
| Integração (FFmpeg real) | CA-20 — duração da saída do `overlay_animation` | `apps/jobs/tests/` |
| Integridade | hash de toda constante alterada; `CoberturaDoCongelamentoTests` continua verde | `test_grok_prompts_integridade.py` |

Com uma exceção, nada precisa de banco real, rede, Whisper ou FFmpeg: `_create_suggestions`
recebe o dict do LLM já pronto e grava em sqlite; a extração de vídeo acontece **depois**,
em `_extract_cuts_for_suggestions`, e fica fora destes testes.

**A exceção é o CA-20**, que só tem valor com FFmpeg de verdade — o bug que ele previne é do
FFmpeg, e mockar a chamada provaria apenas que os argumentos foram montados. Gera um vídeo
sintético de poucos segundos e um asset de overlay maior, roda, mede com `ffprobe`. Se o
binário não estiver disponível no ambiente, o teste é pulado com marcador explícito — e essa
é a única exceção à regra de zero `skip` neste projeto, registrada aqui de propósito.

**O que estes testes não provam:** que o modelo passou a escolher cortes melhores. Isso é
medição em produção (R-4), não teste.

## 14. Checklist de acompanhamento

```
Status: concluído · Progresso: 9/9 PRs · 14/14 itens · atualizado em 2026-08-24
```

### PR 0 — Caracterizar `_create_suggestions`

- [x] **F-00** · Testes de caracterização do comportamento atual
      risco: nenhum · 3h · produção: transparente · 39 testes / 1 arquivo
  - [x] Testes escritos — **passaram na primeira execução**, que é o esperado para caracterização (descrevem o que já existe). Não houve estado vermelho inicial.
  - [x] **Rede validada por mutação**, já que teste que passa de cara também pode ser teste que não afirma nada: mutei `VIRAL_SHORT_MAX_SEC` 60→75, `VIRAL_LONG_MAX_SEC` 15→40 min e `EDUCATIONAL_SHORT_MAX_SEC` 180→150; exatamente 3 testes ficaram vermelhos, um por mutação. Mutação revertida.
  - [x] Implementado
  - [x] Cobertura de `_create_suggestions` (772-1001): **integral, exceto o branch parcial `809->813`** — o `elif getattr(analysis, "id", None)` sem saída falsa alcançável, já que a função grava linhas com FK para a análise e portanto ela sempre tem PK. Não usei `# pragma: no cover`: seria mexer em código de produção num PR só de teste. Fica anotado para quem tocar nesse bloco.
  - [x] `ruff check` limpo — `All checks passed!`
  - [x] `manage.py check --fail-level WARNING` limpo (com `settings_test`; o settings padrão exige psycopg, ausente no venv local — no CI roda com o padrão)
  - [x] Suíte completa verde — 564 passed, cobertura global 49,24% (catraca 45,0)
  - [x] Critérios cobertos: nenhum (é rede de segurança, não regra nova)
  - [x] PR aberto e revisado — #69
  - [x] Mergeado — `bfd34fc` (merge `eb325b2`)
  - [x] Verificado após deploy — PR só de teste, sem efeito em produção
  - Status: concluído · Notas: `analysis_flow.py` saiu de 14% para 37% de cobertura. Três testes marcados com ⚠ existem para mudar nos PRs 1, 5 e 7 — a mudança de asserção é a prova de que a regra mudou de propósito.

### PR 1 — Cortes longos de 8 a 40 minutos

- [x] **F-01** · Constantes unificadas e clamp válido em todos os modos
      risco: médio · 2h · produção: cortes longos maiores · 42 linhas / 1 arquivo
  - [x] Testes escritos primeiro (falharam antes) — as asserções do F-00 ficaram vermelhas antes da mudança de código
  - [x] Implementado — `LONG_CUT_MIN_SEC` / `LONG_CUT_MAX_SEC` fora do `if is_viral_prompt`
  - [x] Cobertura: o bloco de clamp de long fica integral, linha e branch
  - [x] `ruff check` limpo · `manage.py check` limpo · suíte verde (566 passed)
  - [x] Critérios cobertos: CA-01, CA-02, CA-03
  - [x] Asserção de 15 min do F-00 atualizada para 40, com o porquê no commit — mais duas: `duration_minutes` do LLM → calculado, e o mínimo de 8 min passando a valer no educacional
  - [x] PR aberto e revisado — #70 · Mergeado — `cbecad6` (merge `77733a4`) · Verificado após deploy: pendente, efeito só no próximo job analisado
  - Status: concluído · Notas: o mínimo de 8 min passou a descartar long educacional curto, que antes era aceito. Estava em RN-01, mas não estava na lista de mudanças do PR — vale saber ao olhar o primeiro job educacional.
- [x] **F-02** · Texto dos prompts, hashes e doc de estratégia
      risco: baixo · 2h · produção: transparente · 61 linhas / 4 arquivos
  - [x] Hashes atualizados no mesmo PR, com a mudança editorial descrita no commit — 10 hashes, conferidos um a um contra a lista congelada antes de escrever os valores novos
  - [x] `CoberturaDoCongelamentoTests` verde
  - [x] `docs/AUTO_CUTS_STRATEGY.md` atualizado — tabela do que o prompt pede × o que o backend impõe
  - [x] `ruff check` · `manage.py check` · suíte verde
  - [x] Critérios cobertos: CA-01 (lado do prompt)
  - [x] PR aberto e revisado — #70 · Mergeado — `cbecad6` (merge `77733a4`) · Verificado após deploy: pendente
  - Status: concluído · Notas: só os 10 hashes virais mudaram. Nenhum educacional, nenhum bloco de vocabulário — o educacional continua pedindo 20–40 min.

### PR 2 — Sinais do autor

- [x] **F-03** · Blocos `AUTHOR_CUE_RULES_PT` / `_EN` em `vocabulary.py`
      risco: baixo · 2h · produção: transparente · ~390 tokens por requisição
  - [x] Implementado · exportado em `__all__` · hash novo registrado
  - [x] `CoberturaDoCongelamentoTests` verde
  - [x] `ruff check` · `manage.py check` · suíte verde
  - [x] Critérios cobertos: CA-04
  - [x] PR aberto e revisado — #71 · Mergeado — `9edb01e` (merge `7024b19`) · Verificado após deploy: pendente
  - Status: concluído · Notas: texto revisado e aprovado pelo dono do conteúdo antes do merge. Duas regras entraram além do pedido: direção do marcador (frente/trás) e "marcador não salva trecho fraco" — esta segunda para a regra não virar caminho de nota inflada, já que a nota é o que o PR 6 usa para descartar.
- [x] **F-04** · Concatenar nos 7 system prompts e declarar `author_cue` nos 7 templates
      risco: baixo · 3h · produção: transparente · 185 linhas / 6 arquivos
  - [x] Testes escritos primeiro (falharam antes) — `test_prompts_conteudo.py`, arquivo novo
  - [x] Implementado · 16 hashes (14 alterados, 2 novos)
  - [x] Cobertura: os testes novos são de conteúdo de prompt, sem código de produção novo
  - [x] `ruff check` · `manage.py check` · suíte verde (575 passed)
  - [x] Critérios cobertos: CA-04, CA-05
  - [x] PR aberto e revisado — #71 · Mergeado — `9edb01e` (merge `7024b19`) · Verificado após deploy: pendente
  - Status: concluído · Notas: os exemplos de JSON dos 7 templates foram renderizados e parseados com `json.loads` depois da edição. `test_prompts_conteudo.py` cobre o que o hash não cobre — prompt novo que nasça sem a regra.

### PR 3 — Escala 0–100 no modo educacional

- [x] **F-05** · Escala única nos dois prompts educacionais
      risco: baixo · 1h · produção: score educacional passa a 0–100 · 40 linhas / 5 arquivos
  - [x] Testes escritos primeiro (falharam antes)
  - [x] Implementado · 4 hashes, todos educacionais
  - [x] Cobertura: mudança de texto de prompt, sem código de produção novo
  - [x] `ruff check` · `manage.py check` · suíte verde (578 passed)
  - [x] Critérios cobertos: CA-06
  - [x] Convivência de escalas registrada no doc de estratégia — feito no F-11 (#75), na seção Scoring
  - [x] PR aberto e revisado — #72 · Mergeado — `016c00f` · Verificado após deploy: pendente
  - Status: concluído com pendência anotada · Notas: o prompt de corte pronto continua em 1–10 de propósito, agora preso por teste — mudar a escala dele sem mexer na conversão ×10 de `_process_ready_cuts_flow` multiplicaria a nota de novo.

### PR 4 — Calibração honesta da nota

- [x] **F-06** · Blocos de faixa de nota e concatenação nos 7 prompts
      risco: baixo · 3h · produção: distribuição de notas muda · ~290 tokens por requisição
  - [x] Testes escritos primeiro (falharam antes)
  - [x] Implementado · 9 hashes (7 system prompts, 2 constantes novas)
  - [x] Cobertura: mudança de texto de prompt, sem código de produção novo
  - [x] `ruff check` · `manage.py check` · suíte verde (584 passed)
  - [x] Critérios cobertos: CA-07
  - [x] PR aberto e revisado — #73 · Mergeado — `c334c84` · Verificado após deploy: pendente
  - Status: concluído · Notas: nenhum template mudou — calibração é regra de papel do modelo, não de formato. Até o PR 6 o efeito é nota média mais baixa sem ganho visível; isso é esperado, não regressão.

### PR 5 — Margem de candidatos e ordem dos filtros

- [x] **F-07** · `analyze_chunks_in_one_request` recebe a quantidade e valida sobre o pedido
      risco: baixo · 3h · produção: transparente
  - [x] Testes escritos primeiro (falharam antes)
  - [x] Implementado — `max_shorts`/`max_longs`, com o mínimo da validação saindo do pedido
  - [x] Cobertura: código novo coberto
  - [x] `ruff check` · `manage.py check` · suíte verde (593 passed)
  - [x] Critérios cobertos: CA-10
  - [x] PR aberto e revisado — #74 · Mergeado — `4af0ddb` · Verificado após deploy: pendente
  - Status: concluído · Notas: sem isso, pedir 6 e receber 6 seria recusado por "abaixo do mínimo" com o env em 20 — e cada recusa reenvia a transcrição inteira, três vezes.
- [x] **F-08** · Quantidade derivada do alvo do job e filtros antes do corte
      risco: médio · 4h · produção: composição do resultado muda
  - [x] Testes escritos primeiro (falharam antes)
  - [x] Implementado — `_delivery_limits` e `_candidates_to_request`, filtros antes do corte
  - [x] Cobertura: código novo coberto
  - [x] `ruff check` · `manage.py check` · suíte verde
  - [x] Critérios cobertos: CA-08, CA-09, CA-11
  - [x] PR aberto e revisado — #74 · Mergeado — `4af0ddb` · Verificado após deploy: pendente
  - Status: concluído · Notas: ao extrair o helper, a substituição acertou a primeira ocorrência — dentro dele mesmo — e a função ficou recursiva. A suíte inteira passou porque nada a chamava ainda; só os testes novos pegaram.
- [x] **F-09** · `LLM_CANDIDATE_MARGIN` em settings e `.env.example`
      risco: baixo · 1h · produção: transparente até o `.env` mudar
  - [x] Implementado · default 1.5 documentado, e os `LLM_MAX_*` documentados como teto
  - [x] Cobertura: código novo coberto
  - [x] `ruff check` · `manage.py check` · suíte verde
  - [x] Critérios cobertos: CA-08, CA-09 (lado da config)
  - [x] PR aberto e revisado — #74 · Mergeado — `4af0ddb` · Verificado após deploy: pendente
  - Status: concluído · Notas: **ação de rollout pendente** — subir `LLM_MAX_SHORTS=15` e `LLM_MAX_LONGS=8` no `.env` de produção é o que liga a margem para job de alvo grande.

### PR 6 — Filtro de score mínimo

- [x] **F-10** · Descarte por nota, log e mensagem quando zera
      risco: baixo · 4h · produção: desligado por default
  - [x] Testes escritos primeiro (falharam antes) — 9 testes
  - [x] Implementado
  - [x] Cobertura: inclui o caminho de zero cortes e o de nota ausente
  - [x] `ruff check` · `manage.py check` · verde nos apps tocados
  - [x] Critérios cobertos: CA-12, CA-13, CA-14, CA-15
  - [x] PR aberto e revisado — #75 · Mergeado — `499c0ed` · Verificado após deploy: pendente
  - Status: concluído · Notas: **ação de rollout pendente** — o filtro entra desligado (`AUTO_CUT_MIN_VIRALITY_SCORE=0`); ligar depois de ler o log de descarte em jobs reais.
- [x] **F-11** · Documentar calibração do limiar
      risco: nenhum · 1h · produção: transparente
  - [x] `docs/AUTO_CUTS_STRATEGY.md` com como escolher o número a partir do log
  - [x] Critérios cobertos: nenhum (documentação)
  - [x] PR aberto e revisado — #75 · Mergeado — `499c0ed` · Verificado após deploy: pendente
  - Status: concluído · Notas: entrou junto a convivência de escalas do F-05 e um aviso nas três seções que transcrevem os prompts de 2025 — elas falam de uma chamada por chunk e de um prompt de agregação que não existem.

### PR 7 — Teto duro de short e educacional em 150s

- [x] **F-12** · `SHORT_MAX_SEC_HARD` sobre todo short e educacional em 150s
      risco: baixo · 3h · produção: shorts educacionais 30s mais curtos
  - [x] Testes escritos primeiro (falharam antes) — matriz cobrindo os 7 modos + `prompt_version` inexistente e vazio
  - [x] Implementado — teto duro de 170s depois dos ramos por modo
  - [x] Cobertura: o bloco novo fica integral, linha e branch
  - [x] `ruff check` · `manage.py check` · verde (481 passed nos apps tocados)
  - [x] Critérios cobertos: CA-16, CA-17, CA-18
  - [x] Asserção de 180s do F-00 atualizada para 150s, e a de `prompt_version` desconhecido de 400s para 170s
  - [x] PR aberto e revisado — #76 · Mergeado — `27187b8` · Verificado após deploy: pendente
  - Status: concluído · Notas: 170 fica acima da maior faixa em uso (viral_long, 160s), então não altera nenhum modo existente — é rede, não regra.
- [x] **F-13** · Faixa 120–150s nos dois prompts educacionais
      risco: baixo · 2h · produção: transparente · 11 pontos de texto
  - [x] Testes escritos primeiro (falharam antes)
  - [x] Implementado · 4 hashes, todos educacionais
  - [x] Cobertura: mudança de texto de prompt, sem código de produção novo
  - [x] `ruff check` · `manage.py check` · verde
  - [x] Critérios cobertos: CA-19
  - [x] `docs/AUTO_CUTS_STRATEGY.md` atualizado — tabela de shorts, teto duro e o porquê dos 150s
  - [x] PR aberto e revisado — #76 · Mergeado — `27187b8` · Verificado após deploy: pendente
  - Status: concluído · Notas: o exemplo de duração saiu de 150 para 140 — exemplo colado no teto ensina o modelo a colar no teto.

### PR 8 — Duração de saída determinística no overlay

- [x] **F-14** · `-map [outv] -map 0:a? -shortest` no `overlay_animation`
      risco: **era baixo, virou alto** · 2h · produção: overlay animado deixa de travar
  - [x] Teste escrito primeiro (falhou antes) — asset cinco vezes mais longo que o clipe, com e sem áudio
  - [x] Implementado
  - [x] Cobertura: os dois caminhos (asset animado e estático) exercitados
  - [x] `ruff check` · `manage.py check` · verde (280 passed nos apps tocados)
  - [x] Critérios cobertos: CA-20
  - [ ] **Finalização real com overlay animado conferida visualmente (R-9) — pendente, exige job real com asset animado**
  - [x] PR aberto e revisado — #77 · Mergeado — `a9518b1` · Verificado após deploy: pendente
  - Status: concluído com verificação visual pendente · Notas: o plano classificou como risco condicional ao asset ter áudio. **Estava errado**: reproduz com qualquer asset animado, e não alonga — trava. 1h38 de vídeo gerado em 25s de relógio, arquivo com `moov atom not found`, worker preso.

### Mapa critério → item

| Critério | Item |
| --- | --- |
| CA-01 | F-01, F-02 |
| CA-02 | F-01 |
| CA-03 | F-01 |
| CA-04 | F-03, F-04 |
| CA-05 | F-04 |
| CA-06 | F-05 |
| CA-07 | F-06 |
| CA-08 | F-08, F-09 |
| CA-09 | F-08, F-09 |
| CA-10 | F-07 |
| CA-11 | F-08 |
| CA-12 | F-10 |
| CA-13 | F-10 |
| CA-14 | F-10 |
| CA-15 | F-10 |
| CA-16 | F-12 |
| CA-17 | F-12 |
| CA-18 | F-12 |
| CA-19 | F-13 |
| CA-20 | F-14 |

Nenhum critério órfão. F-00 e F-11 não cobrem critério de propósito: são rede de segurança e
documentação.

### Registro de execução

| Data | PR | O que mudou | Surpresas |
| --- | --- | --- | --- |
| 2026-08-24 | PR 8 (#77, `a9518b1`) | `-shortest` e `-map` explícito no `overlay_animation` | O plano classificou como risco condicional ao asset ter áudio. Errado nas duas pontas: acontece com qualquer asset animado, e o efeito não é alongar — é **travar**. 1h38 de vídeo em 25s de relógio, arquivo inválido, worker preso. Asset estático nunca teve o problema, que é por que passou despercebido. |
| 2026-08-24 | PR 7 (#76, `27187b8`) | Teto duro de 170s e educacional 120–150s | Nenhuma surpresa técnica. O teste que percorre `prompt_version.choices` é o que faz modo novo nascer coberto. |
| 2026-08-24 | PR 6 (#75, `499c0ed`) | Filtro de score mínimo, desligado por default; seção Scoring no doc | Achado fora de escopo: `settings_test` define `CELERY_BROKER_URL=memory://` mas o override não chega em `app.conf.broker_url`, que segue em `redis://127.0.0.1:6379`. Seis testes falham localmente quando o Redis cai; passam no CI, o que ainda não está explicado. Vale PR próprio. |
| 2026-08-24 | PR 5 (#74, `4af0ddb`) | Margem de candidatos e filtros antes do corte de quantidade | Ao extrair `_delivery_limits`, a substituição acertou a primeira ocorrência — dentro do próprio helper — e ele ficou chamando a si mesmo. A suíte inteira passou porque nada chamava o helper ainda; só os testes novos pegaram, com `RecursionError`. |
| 2026-08-24 | PR 4 (#73, `c334c84`) | Calibração da nota com 4 faixas nos 7 prompts | A instrução de cota ("retorne EXATAMENTE N itens") e a nota honesta se contradizem por natureza. A contrapartida virou linha explícita no prompt, em vez de esperar que o modelo resolvesse sozinho. |
| 2026-08-24 | PR 3 (#72, `016c00f`) | Escala 0–100 nos dois prompts educacionais | Nenhuma. O registro da convivência de escalas no doc de estratégia ficou pendente e foi anexado ao F-11, para não abrir um PR de documentação sozinho. |
| 2026-08-24 | PR 2 (#71, `7024b19`) | Bloco de sinais do autor nos 7 prompts; campo `author_cue`; `test_prompts_conteudo.py` | O congelamento de hash não pega prompt que **nasce** sem uma regra — só pega prompt que muda. Faltava a outra metade, e ela virou arquivo próprio. |
| 2026-08-24 | PR 1 (#70, `77733a4`) | Cortes longos 8–40 min em todos os modos; `duration_minutes` sempre do timecode; 10 hashes de prompt | O mínimo de 8 min, ao deixar de ser exclusivo dos modos virais, passou a **descartar** long educacional abaixo de 8 min — que antes era aceito. Está previsto em RN-01, mas não estava escrito na descrição do PR 1 no plano. |
| 2026-08-24 | PR 0 (#69, `eb325b2`) | 39 testes de caracterização de `_create_suggestions`; cobertura do arquivo de 14% para 37% | Os testes passaram todos na primeira execução — nenhuma previsão minha sobre o comportamento atual estava errada. Como isso também é o sintoma de teste que não afirma nada, validei a rede por mutação de três constantes. Uma descoberta ficou registrada em teste: em contexto de factory, com alvo 2 e os dois primeiros colocados sem brand mapeada, o job entrega **zero** short mesmo tendo três candidatos válidos na fila. |

## 15. Rollout e rollback

**Nenhum PR exige janela, parada ou migração.**

1. PR 0 vai sozinho e não muda nada em produção.
2. PRs 1 a 4 têm efeito no **próximo job analisado** — não há reprocessamento retroativo.
   Depois do PR 1, acompanhar a duração média dos longos e o tempo de finalização (R-1).
3. PR 5 só ativa a margem de verdade se `LLM_MAX_SHORTS` / `LLM_MAX_LONGS` subirem no `.env`
   de produção (sugestão: 15 e 8). **Sem mexer no `.env`, o comportamento continua o de
   hoje para job de alvo 10** — job de alvo menor já passa a pedir menos, o que é economia
   imediata.
4. PR 6 entra desligado (`AUTO_CUT_MIN_VIRALITY_SCORE=0`). Ligar depois de olhar, em alguns
   jobs reais, a distribuição de notas no log. Sugestão de partida: 60, subindo aos poucos.
5. PRs 7 e 8 podem ir a qualquer momento depois do PR 0 — não dependem do resto da cadeia.
   Depois do PR 7, conferir num job educacional real que os shorts saem com 150s e que o
   YouTube os classifica como Short. Depois do PR 8, conferir uma finalização com overlay
   animado (R-9).

**Como saber que está saudável:** os jobs seguem terminando em `status="done"`; a contagem
de cortes por job não desaba; o log não mostra descarte em massa por duração; o custo por
modelo no Prometheus não dá salto inesperado.

**Rollback:** revert do PR. Prompt é texto — o revert restaura o hash junto. As duas
variáveis novas voltando ao default desligam o efeito sem deploy de código.

## 16. Suposições e questões em aberto

### Suposições

- **S-01** — Os dois modos educacionais continuam **pedindo** 20–40 min de corte longo,
  dentro do teto novo de 8–40. Preservar a intenção editorial de que aula longa é longa.
- **S-02** — `author_cue` fica fora do `metadata_sanitizer` porque não é publicado em lugar
  nenhum. Se um dia aparecer para o público, entra no sanitizer antes.
- **S-03** — A margem de 1,5× é chute inicial calibrável por env, não número medido. O
  primeiro dado real vem do log de quantos candidatos caem por filtro.
- **S-04** — Nenhum consumidor externo depende de corte longo ter no máximo 15 min. Verifiquei
  frontend, serializers, `factory_scheduler` e `youtube_description` — nenhum tem o valor
  fixado. Se existir integração fora do repositório, esta suposição cai.
- **S-05** — A faixa do viral_long (80–160s) continua como está. Tem 20s de folga contra o
  limite de 3 min e não houve relato de estouro nesse modo — ao contrário do educacional.

### Decisões registradas

- **Q-01 · resolvida em 24/08** — o teto duro de short **entra** neste projeto, como PR 7,
  com o educacional caindo de 180s para 150s: 180 tangencia o limite do YouTube e em
  produção às vezes o ultrapassa, fazendo o corte ser tratado como vídeo longo. O
  `-shortest` do overlay entra como PR 8. O alinhamento do fim do corte ao fim de segmento
  **não** entra — muda a fronteira de todo corte e merece decisão própria.

### Questões em aberto

- **Q-02** — `shorts_target` aceita até 30 mas a entrega segue limitada a 10. Fica assim, ou
  vira projeto próprio com o custo de ffmpeg medido antes?

---

> Ao concluir qualquer item, atualize este arquivo: marque as caixas, registre o hash e
> atualize o progresso. Se a realidade divergir do plano, corrija o plano.
