# Feature — Botão "Postar Imediato" (Factory / aba Agendamento)

| | |
|---|---|
| **Início** | 2026-08-15 |
| **Base** | `develop` (`8d822c3`) |
| **Escopo** | aba Agendamento da Factory: renomear o botão atual e criar o de publicação imediata |
| **Exige parada de produção** | não |

---

## ⏸ PONTO DE RETOMADA

**Estado: PR 1 (rename) em andamento.** Nada mergeado ainda.

Próximo passo concreto: fechar o PR 1, depois atacar o PR 2 (backend).

---

## 1. O que o usuário pediu

Dois botões na aba Agendamento, onde hoje existe um só:

1. **"Criar Agendamento"** — é o botão de hoje, apenas renomeado. Comportamento inalterado:
   gera a agenda do dia escolhido e deixa a publicação para o beat, no horário de cada slot.
2. **"Postar Imediato"** — novo. Mesmo modal de data, **mesma lógica** de gerar slots por
   brand e casar com os vídeos disponíveis no banco, mas em vez de parar em `PENDING`, já
   envia para YouTube e Upload-Post seguindo as regras de publicação que já existem.

### Decisões do usuário (2026-08-15)

| Pergunta | Resposta |
| --- | --- |
| O que acontece com o horário do slot? | **Publica tudo agora**, ignorando o horário do slot |
| Slot cujo horário já passou? | **Não incluir** |
| Modal mostra prévia antes de confirmar? | **Sim** |

**Leitura combinada das duas primeiras** (é assim que está sendo construído): os slots
continuam decidindo **quantos e quais** vídeos entram, e slot vencido fica de fora; os que
entram são **publicados agora**, não no horário do slot.

- Clicar às 14h de hoje escolhendo **amanhã** → os 3 slots de amanhã são futuros, os 3
  vídeos são publicados **agora**.
- Clicar às 14h de hoje escolhendo **hoje** → slots de 09h e 13h ficam de fora; só o de
  18h é publicado, **agora**.

> ⚠ **Consequência aceita pelo usuário, com o aviso na tela de escolha:** os vídeos de uma
> mesma brand vão ao ar em sequência, no mesmo intervalo de poucos minutos. É exatamente o
> que os slots existem para evitar. `UPLOAD_INTERVAL_SECONDS = 60` **não** protege aqui —
> ele só espaça uma brand da outra, não os vídeos dentro da mesma brand
> (`apps/social/tasks.py:309`, o loop é sequencial e sem espera).

---

## 2. Como funciona hoje (levantado antes de mexer)

### O botão atual

`POST /factories/{id}/trigger-immediate-schedule/` (`apps/api/views/factories.py:31`)
chama `generate_daily_schedule_for_factory(..., enqueue_immediately=True)`.

**`enqueue_immediately` não enfileira nada.** O nome engana. A flag tem um único efeito, em
`apps/jobs/services/factory_scheduler.py:298`:

```python
if not enqueue_immediately and slot_local < now_local:
    continue
```

Só isso: **não descarta os slots do dia cujo horário já passou.**

O que é criado (`factory_scheduler.py:177-241`): `ScheduledPost` com `status="PENDING"`,
`scheduled_at` = horário do slot e `privacy_status="private"`; `FactoryPostingSchedule` em
`PLANNED`; item do inventário em `SCHEDULED`. **Nenhum `.delay()`.**

Detalhe de UX que já existe: o botão **já abre modal de data** (`Agendamento.jsx:877`), com
default em *amanhã* (`factories.py:49`) e `min` = hoje.

### Quem publica

`check_scheduled_posts_task`, no beat a cada 60s (`config/celery.py:12-16`):

- pega `PENDING` com `scheduled_at <= now`, no máximo **20 por tick** (`BATCH_LIMIT_PER_TICK`);
- mais os posts **só-YouTube** dentro da janela de **1h antes** do slot
  (`YOUTUBE_PREPUBLISH_WINDOW_SECONDS`, `apps/social/tasks.py:119`);
- agrupa por brand e chama `process_brand_posting_queue_task(brand_id, post_ids)`
  (`tasks.py:284`), que itera chamando `_run_post_to_platforms(post_id)`.

### Regras de publicação por plataforma

| | Slot no futuro | Slot já passou |
| --- | --- | --- |
| **YouTube** | privado + `publishAt` nativo; o YouTube abre sozinho no horário (`publishers/youtube.py:110-128`) | **sem `publishAt`** — a API rejeita data no passado (`youtube.py:342`) |
| **Upload-Post** | `scheduled_date` no fuso da brand (`publishers/upload_post.py:183-220`) | omite `scheduled_date` e publica direto (`upload_post.py:60`) |

Longos `YTB` têm 30% de chance de sair `public` direto, sem `publishAt`
(`LONG_DIRECT_PUBLIC_PROBABILITY`, `youtube.py:30`). Combinado com a janela de 1h, esses
30% vão ao ar **até uma hora antes** do slot planejado.

### 🐛 Bug encontrado no levantamento

Quando não há `publishAt`, `_resolve_publish_mode` devolve `(None, None)`
(`youtube.py:357-364`), então nenhum `privacy_override` é aplicado e vale o
`privacy_status` do post — que o scheduler cria **fixo como `"private"`**
(`factory_scheduler.py:186`). Resultado:

> **O vídeo sobe no YouTube privado e fica privado para sempre.** Não existe nenhum
> `videos().update()` de privacidade em lugar nenhum do repositório — procurado, não há.

Isso **já afeta o botão de hoje** sempre que se escolhe o dia corrente com slots vencidos
(que é justamente o que a flag `enqueue_immediately` habilita). E derrubaria o "Postar
Imediato" inteiro, porque nele nunca há `publishAt`.

**Correção adotada:** no caminho imediato o `ScheduledPost` nasce com
`privacy_status="public"`.

---

## 3. Desenho

### Regras do caminho imediato

1. **Slot vencido não entra** — mesma regra do `enqueue_immediately=False` de hoje.
2. **`scheduled_at` nasce como `now`**, não como o horário do slot. É isso que faz
   `_get_publish_at()` devolver `None` e `_format_scheduled_date()` devolver `None`, ou
   seja: é o que faz "publicar agora" realmente acontecer. O horário original do slot fica
   preservado em `FactoryPostingSchedule.scheduled_at`.
3. **`privacy_status` nasce `public`** — sem isso, ver o bug acima.
4. **Envio reusa `process_brand_posting_queue_task`**, a mesma task do beat. Nenhuma regra
   de publicação nova, nenhuma cópia de `_run_post_to_platforms`.
5. **Execução assíncrona.** Upload de vídeo não cabe num request HTTP; a API responde com a
   contagem enfileirada e a UI acompanha pelo status dos posts.

### Endpoints

| Método | Rota | O que faz |
| --- | --- | --- |
| `POST` | `/factories/{id}/immediate-post-preview/` | roda o planejamento e devolve o que **seria** postado, por brand. Não cria alocação |
| `POST` | `/factories/{id}/trigger-immediate-post/` | cria os posts e dispara o envio |

### Refatoração necessária

`generate_daily_schedule_for_factory` hoje planeja e grava no mesmo passo. Para a prévia
existir sem efeito colateral, separar:

- `plan_daily_schedule_for_factory(...) -> list[PlannedAllocation]` — decide slots elegíveis
  e casa com o estoque, **sem persistir alocação**;
- `generate_daily_schedule_for_factory(...)` — consome o plano e persiste, como hoje.

> ⚠ **Efeito colateral que a prévia não consegue evitar:** `DailyPostingPlanService.get_or_generate_for_day`
> (`factory_scheduler.py:273`) **grava** o plano diário se ele ainda não existe. Uma prévia
> de um dia ainda não planejado cria esse `DailyPostingPlan`. É idempotente e é o mesmo
> plano que o agendamento automático usaria depois — decidido aceitar, mas está registrado
> aqui porque não é óbvio.

---

## 4. Checklist

### PR 1 · Renomear o botão para "Criar Agendamento"

Branch `feat/renomear-botao-criar-agendamento` · risco: nenhum · só frontend

- [x] Botão da brand (`Agendamento.jsx:512`) e da factory (`:559`) renomeados
- [x] `title` dos dois explicando que a publicação é no horário do slot
- [x] Modal: título e botão de confirmação coerentes com o novo nome
- [x] Verificado que nenhum teste depende do texto (só há 2 testes, ambos de `utils/`)
- [x] `npm test` (8 passam) + `npm run build` verdes
- [ ] PR aberto · [ ] CI verde · [ ] Mergeado

### PR 2 · Backend do "Postar Imediato"

Branch a criar · risco: médio · pré-requisito: nenhum (independe do PR 1)

- [ ] `plan_daily_schedule_for_factory` extraída, sem persistir alocação
- [ ] `generate_daily_schedule_for_factory` passa a consumir o plano — **comportamento
      atual inalterado**, provado por teste
- [ ] Endpoint de prévia
- [ ] Endpoint de execução: `scheduled_at=now`, `privacy_status="public"`, slot vencido fora
- [ ] Dispara `process_brand_posting_queue_task` por brand
- [ ] Idempotência: clicar duas vezes não publica duas vezes (o claim
      `PENDING → POSTING` de `preflight.py:154` já protege — **confirmar com teste**)
- [ ] Testes: prévia bate com o que a execução faz; slot vencido não entra; post nasce
      `public` e com `scheduled_at` no presente
- [ ] `ruff check .` + suíte sob `settings_test`
- [ ] PR aberto · [ ] CI verde · [ ] Mergeado

### PR 3 · Frontend do "Postar Imediato"

Branch a criar · risco: baixo · pré-requisito: PR 2

- [ ] Botão "Postar Imediato" ao lado de "Criar Agendamento", nos dois lugares
- [ ] Modal de data reaproveitado, em modo "postar"
- [ ] Prévia carregada ao escolher a data, com total por brand e aviso de brand sem estoque
- [ ] Botão de confirmação mostra o número: "Postar N vídeos"
- [ ] Estado de carregando e bloqueio de duplo clique
- [ ] `npm test` + `npm run build` verdes
- [ ] PR aberto · [ ] CI verde · [ ] Mergeado

### Fora do escopo desta feature (mas encontrado nela)

- [ ] O bug do vídeo privado eterno **também afeta o botão de agendamento** quando se
      escolhe o dia corrente. A correção aqui cobre só o caminho novo. Corrigir o caminho
      antigo é decisão à parte — muda comportamento de um fluxo em produção.
- [ ] Renomear `enqueue_immediately` para algo que descreva o que a flag faz de fato
      (`include_past_slots`). Cosmético, mas o nome já custou uma investigação.

---

## 5. Registro de execução

| Data | O que mudou | Surpresa |
| --- | --- | --- |
| 2026-08-15 | Levantamento do fluxo do botão atual, do beat e das duas plataformas | O botão nunca enviou nada — `enqueue_immediately` só deixa passar slot vencido. E o caminho que ele habilita sobe vídeo privado **para sempre**: sem `publishAt`, o `private` fixo do scheduler nunca é revertido por ninguém |
