# Plano: Postagem Automática no YouTube (Longos + Shorts)

## Objetivo

Implementar postagem automática no YouTube, cobrindo:
- **YouTube Shorts (YT)**: vídeos verticais até 60s (ou 90s conforme política atual)
- **YouTube Longos (YTB)**: vídeos longos (16:9 ou 9:16)

Ambos usam a mesma **YouTube Data API v3** e o mesmo fluxo de OAuth.

---

## Situação atual

| Item | Status |
|------|--------|
| ScheduledPost | ✅ Existe (job, platforms, scheduled_at, status, title, description, tags, privacy_status) |
| PLATFORM YT, YTB | ✅ Definidos no Job |
| Celery Beat | ✅ Configurado (check a cada 1 min) |
| Task de postagem | ✅ check_scheduled_posts_task, post_to_platforms_task |
| BrandSocialAccount | ✅ brand, platform, channel_id, tokens |
| OAuth YouTube | ✅ connect, callback, select-channel |
| Publisher YouTube | ✅ Upload real via videos.insert |

---

## Fases de implementação

### Fase 1 – Infraestrutura base

| # | Tarefa | Descrição |
|---|--------|-----------|
| 1.1 | Celery Beat | Configurar `CELERY_BEAT_SCHEDULE` para rodar a cada 1 min |
| 1.2 | Task `check_scheduled_posts_task` | Busca ScheduledPost com status=PENDING e scheduled_at <= now |
| 1.3 | Task `post_to_platforms_task` | Recebe scheduled_post_id, itera plataformas, chama publisher |
| 1.4 | Modelo `BrandSocialAccount` | brand, platform, account_id, account_name, **channel_id** (YouTube), access_token, refresh_token, expires_at |

### Fase 2 – OAuth YouTube (Google)

| # | Tarefa | Descrição |
|---|--------|-----------|
| 2.1 | Google Cloud Project | Criar projeto, habilitar YouTube Data API v3 |
| 2.2 | OAuth 2.0 credentials | Tipo "Web application" ou "Desktop" (client_id, client_secret) |
| 2.3 | Endpoint `/api/youtube/connect/` | Inicia fluxo OAuth, redireciona para Google |
| 2.4 | Callback `/api/youtube/callback/` | Recebe code, troca por tokens, chama `channels.list(mine=true)` para listar canais |
| 2.5 | Seleção de canal | Usuário escolhe qual canal vincular; salva `channel_id` em BrandSocialAccount |
| 2.6 | Refresh token | YouTube usa refresh_token; renovar access_token quando expirar |
| 2.7 | Variáveis .env | GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI |

### Fase 3 – Publisher YouTube

| # | Tarefa | Descrição |
|---|--------|-----------|
| 3.1 | App `apps/social/` | Novo app para publishers |
| 3.2 | `YouTubePublisher` | Classe com `publish(scheduled_post, account, metadata) -> video_id` |
| 3.3 | Upload via `videos.insert` | google-api-python-client + MediaFileUpload |
| 3.4 | Diferenciar YT vs YTB | YT: vídeo ≤60s vertical; YTB: qualquer duração. Mesma API, metadata diferente |
| 3.5 | Metadata | title, description, tags, categoryId, privacyStatus, madeForKids |
| 3.6 | Agendamento nativo | publishAt (só com privacyStatus=private) – opcional |

### Fase 4 – Integração com fluxo existente

| # | Tarefa | Descrição |
|---|--------|-----------|
| 4.1 | Vídeo para upload | Job → RenderOutput (vídeo final) ou AutoCutCorte (cortes finalizados) |
| 4.2 | ScheduledPost → Job | ScheduledPost já tem FK para Job; Job tem target_platforms |
| 4.3 | Escolher conta | ScheduledPost ou Job: qual BrandSocialAccount usar (por brand) |
| 4.4 | Campos de metadata | Adicionar title, description, tags em Job ou ScheduledPost |
| 4.5 | Auto-cuts | Permitir agendar postagem de cortes finalizados (AutoCutCorte) |

### Fase 5 – Frontend

| # | Tarefa | Descrição |
|---|--------|-----------|
| 5.1 | Tela "Contas conectadas" | Por marca: listar contas, botão "Conectar YouTube" |
| 5.2 | Fluxo OAuth | Botão → redirect Google → callback → sucesso/erro |
| 5.3 | Agendamento | Ao criar ScheduledPost: escolher conta YouTube (se houver) |
| 5.4 | Metadata | Campos title, description, tags no formulário de agendamento |
| 5.5 | Cortes automáticos | Botão "Agendar postagem" nos cortes finalizados |

---

## Fluxo de dados

```
┌──────────────────────────────────────────────────────────────────┐
│  Usuário agenda postagem                                           │
│  → ScheduledPost(job=X, platforms=["YT"], scheduled_at=...)        │
│  → Ou: agendar corte do AutoCut (novo fluxo)                      │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Celery Beat (a cada 1 min)                                       │
│  → check_scheduled_posts_task()                                   │
│  → Filtra: status=PENDING, scheduled_at <= now                    │
│  → Para cada: post_to_platforms_task.delay(scheduled_post_id)     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  post_to_platforms_task                                           │
│  → Para cada platform em ["YT", "YTB"]:                           │
│  → BrandSocialAccount.objects.get(brand=..., platform=platform)    │
│  → YouTubePublisher.publish(scheduled_post, account, metadata)    │
│  → Atualiza status=DONE ou FAILED, posted_at                       │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  YouTubePublisher.publish()                                        │
│  → Obtém vídeo: Job.render_outputs ou AutoCutCorte.file          │
│  → Refresh token se expirado                                       │
│  → videos.insert (snippet, status, media)                          │
│  → Retorna video_id                                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## Dependências Python

```
google-auth>=2.0.0
google-auth-oauthlib>=1.0.0
google-api-python-client>=2.0.0
```

---

## Diferenças YT (Shorts) vs YTB (Longos)

| Aspecto | YouTube Shorts (YT) | YouTube Longos (YTB) |
|---------|---------------------|----------------------|
| Duração | ≤ 60s (ou 90s) | Sem limite |
| Formato | Vertical 9:16 | Qualquer |
| API | Mesma (videos.insert) | Mesma |
| Metadata | title com #Shorts ajuda descoberta | title, description mais longos |
| Thumbnail | Opcional | Recomendado |

**Implementação**: Um único `YouTubePublisher`; a diferença é só metadata e validação (duração/formato).

---

## Ordem sugerida (sprints)

| Sprint | Itens | Entregável |
|--------|-------|------------|
| 1 | 1.1 a 1.4 | Beat rodando, modelo de contas, task stub |
| 2 | 2.1 a 2.6 | OAuth funcionando, conta conectada por marca |
| 3 | 3.1 a 3.6 | Publisher YouTube real, upload funcionando |
| 4 | 4.1 a 4.5 | Integração Job → vídeo, metadata em ScheduledPost |
| 5 | 5.1 a 5.5 | Frontend: conectar conta, agendar, postar |

---

## Múltiplos canais no mesmo Gmail

Um usuário Google pode ter vários canais YouTube (incluindo "contas de marca"). Para direcionar ao canal certo:

1. **Após OAuth**: Chamar `channels.list(part="snippet", mine=true)` – retorna todos os canais do usuário autenticado.
2. **Lista de canais**: Cada item tem `id` (channel_id) e `snippet.title` (nome do canal).
3. **Vincular por canal**: Cada `BrandSocialAccount` armazena um `channel_id` específico.
4. **Uma conta = um canal**: Uma conexão = um canal. Se a marca usa 3 canais, cria 3 BrandSocialAccount (todos com o mesmo access_token do Gmail, mas channel_id diferente).
5. **No agendamento**: Ao criar ScheduledPost, o usuário escolhe qual canal (qual BrandSocialAccount) usar.

**Modelo**:
```python
# BrandSocialAccount
channel_id = models.CharField(max_length=64, blank=True)  # YouTube: UCxxxxxx
account_name = models.CharField(...)  # Nome exibido: "Canal Principal", "Canal EN"
```

**Fluxo UI**:
1. Clicar "Conectar YouTube" → OAuth
2. Após callback: mostrar lista de canais retornados
3. Usuário seleciona "Vincular canal X à marca Y"
4. Salva BrandSocialAccount(brand=Y, platform=YT, channel_id=UCxxx, account_name="Canal X")
5. Para adicionar outro canal: repetir OAuth (reutiliza tokens) → escolher outro canal

---

## Pontos de atenção

1. **Quota YouTube API**: 10.000 unidades/dia; `videos.insert` = 1600 unidades. ~6 uploads/dia por projeto.
2. **Tokens**: Armazenar com segurança; considerar criptografia em produção.
3. **Vídeo acessível**: Upload direto do arquivo (não precisa URL pública).
4. **Auto-cuts**: ScheduledPost hoje é por Job; pode ser necessário `ScheduledPost(analysis, corte, ...)` ou estender o modelo.
5. **Shorts 60s vs 90s**: YouTube ampliou para 90s; validar limite atual na documentação.

---

## Configuração (.env)

Adicione ao `.env` para OAuth YouTube:

```
GOOGLE_CLIENT_ID=seu_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=seu_client_secret
YOUTUBE_REDIRECT_URI=http://localhost:8000/api/youtube/callback/
FRONTEND_URL=http://localhost:5173
```

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com/)
2. Habilite a **YouTube Data API v3**
3. Crie credenciais OAuth 2.0 (tipo "Aplicativo da Web")
4. Em "URIs de redirecionamento autorizados", adicione `http://localhost:8000/api/youtube/callback/`

## Rodar Celery Beat

Para agendar postagens, inicie o Beat além do worker:

```bash
# Terminal 1: Worker
python -m celery -A config worker -l INFO -P solo

# Terminal 2: Beat (agenda check a cada 1 min)
python -m celery -A config beat -l INFO
```

Ou use `start_celery_beat.bat` no Windows.
