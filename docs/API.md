# API HTTP — Social Automation

Referência para integrações e onboarding. **Contrato canónico:** código em `apps/api/` (viewsets, serializers). Este documento resume rotas e regras; campos exactos dos JSON estão nos serializers.

**Prefixo base:** `{ORIGIN}/api/`  
Ex.: desenvolvimento `http://127.0.0.1:8000/api/`, produção conforme `ALLOWED_HOSTS` e HTTPS.

**Formato:** `Content-Type: application/json` (exceto *uploads* multipart indicados abaixo).

---

## Autenticação

A API usa **JWT** (SimpleJWT), por omissão com `IsAuthenticated` nos viewsets.

| Método | Caminho | Corpo / notas |
|--------|---------|----------------|
| `POST` | `/api/auth/token/` | `{"username": "...", "password": "..."}` → `access`, `refresh` |
| `POST` | `/api/auth/token/refresh/` | `{"refresh": "..."}` → novo `access` |
| `POST` | `/api/register/` | Registo público: `username`, `password` (mín. 8), `email` opcional |

**Cabeçalho nas rotas protegidas:**

```http
Authorization: Bearer <access>
```

---

## OAuth YouTube (callbacks)

Incluídas em `apps.api.urls` sob `/api/youtube/`:

| Caminho | Descrição |
|---------|-----------|
| `GET/POST` | `/api/youtube/connect/` | Início OAuth marca |
| `GET` | `/api/youtube/callback/` | Callback OAuth |
| … | `/api/youtube/factory-check-connect/`, `factory-check-callback/`, `pending-channels/`, `select-channel/` | Fluxos de *factory check* e canais pendentes |

Detalhe de parâmetros: `apps/social/views.py`.

---

## Recursos REST (router)

O `DefaultRouter` expõe listagem e detalhe. Convenção:

- `GET /api/<recurso>/` — lista  
- `POST /api/<recurso>/` — cria (onde permitido)  
- `GET /api/<recurso>/{id}/` — detalhe  
- `PATCH /api/<recurso>/{id}/` — actualização parcial  
- `PUT` — só onde o viewset não restringe métodos  
- `DELETE /api/<recurso>/{id}/` — remover (onde permitido)

### Tabela de recursos (`apps/api/urls.py`)

| Prefixo recurso | ViewSet | Notas |
|-----------------|---------|--------|
| `register` | Registo | Apenas **POST** (criação de utilizador) |
| `factories` | Factory | **Sem DELETE**; GET, POST, PATCH |
| `search-channels` | SearchChannel | Filtro comum: `?factory=` |
| `brands` | Brand | `?factory=` |
| `brand-assets` | BrandAsset | Upload multipart para ficheiros |
| `social-accounts` | BrandSocialAccount | **GET, DELETE** apenas |
| `brand-youtube-credentials` | BrandYouTubeCredential | `?brand=` |
| `sources` | SourceVideo | Vídeo fonte do utilizador |
| `cuts` | Cut | Cortes; ver acções `upload` e *bulk* em `create` |
| `jobs` | Job | Jobs de render; filtros `?brand=`, `?archived=` |
| `scheduled-posts` | ScheduledPost | Agendamentos |
| `video-inventory` | VideoInventoryItem | **Só leitura** + acções abaixo |
| `factory-schedules` | FactoryPostingSchedule | **Só leitura** |
| `posted-videos` | PostedVideoLog | **Só leitura** |
| `auto-cuts` | AutoCutAnalysis | **Sem PUT/PATCH** no recurso principal; GET, POST, DELETE; `create` custom |
| `auto-cut-suggestions` | AutoCutSuggestion | ViewSet fino: ver acções |
| `auto-cut-cortes` | AutoCutCorte | GET, PATCH, POST, DELETE (sem PUT por padrão) |

---

## Acções customizadas (`@action`)

Rotas sob `/api/<recurso>/{id}/` (ou `detail=False` na raiz do recurso). Métodos indicados.

### Factory (`/api/factories/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `.../{id}/trigger-immediate-schedule/` | Agenda imediata; body opcional `target_date`, `brand_id` |
| `GET` | `.../{id}/youtube-check-connect-url/` | URL OAuth para credenciais *YOUTUBE_CHECK_* |

### Brand (`/api/brands/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `.../{id}/trigger-immediate-schedule/` | Idem agendamento por marca |
| `GET` | `.../{id}/social_accounts/` | Lista contas sociais |
| `GET` | `.../{id}/youtube_connect_url/` | URL OAuth YouTube (`?youtube_credential_id=` opcional) |
| `PATCH` | `.../{id}/youtube-description/` | `youtube_description_extra`, `youtube_made_for_kids` |

### Source (`/api/sources/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `.../{id}/extract_cuts/` | Body `cuts: [{start_tc, end_tc, name?, format?}]` — extrai cortes e remove o source |

### Cuts (`/api/cuts/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `/api/cuts/upload/` | Multipart: upload de corte pronto |

### Jobs (`/api/jobs/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `/api/jobs/upload/` | Multipart: vídeo pronto → job DONE com output |
| `GET` | `.../{id}/download/` | Download do ficheiro exportado |
| `POST` | `.../{id}/generate-subtitles/` | Inicia Whisper |
| `PATCH` | `.../{id}/subtitles/` | Edita `segments` / `style` |
| `POST` | `.../{id}/burn-subtitles/` | Queima legendas |
| `POST` | `.../{id}/run/` | Enfileira `process_job` (Celery) |

### Scheduled posts (`/api/scheduled-posts/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `.../{id}/reschedule/` | Reagendar falhos (`scheduled_at`) |
| `POST` | `.../{id}/remove-awaiting/` | Remove agendamento + inventário associado |

### Video inventory (`/api/video-inventory/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `.../{id}/remove-awaiting/` | Remove item aguardando + mídias |
| `POST` | `.../{id}/retry-posting/` | Reativa postagem (`scheduled_at` opcional) |
| `GET` | `.../{id}/download-media/` | ZIP com vídeo, thumb e texto título/descrição |
| `POST` | `.../{id}/mark-posted/` | Marca como postado manualmente |

Filtros úteis na lista: `?factory=`, `?brand=`, `?status=`, `?video_type=SHORT|LONG`.

### Auto cuts — análise (`/api/auto-cuts/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `/api/auto-cuts/upload-ready-cuts/` | Multipart: vários vídeos prontos (`files`, `brand`, `name`, …) |
| `POST` | `/api/auto-cuts/reset-stuck/` | Marca análises presas como erro |
| `POST` | `/api/auto-cuts/delete-stuck/` | Remove jobs presos e ficheiros |
| `POST` | `.../{id}/finalizar/` | Finaliza cortes (corpo com opções de legenda, vertical, overlay, …) |
| `POST` | `.../{id}/bulk-schedule/` | Agenda vários cortes numa janela temporal |

### Auto cut suggestions (`/api/auto-cut-suggestions/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `DELETE` | `.../{id}/` | Remove sugestão |
| `POST` | `.../{id}/create-cut/` | Resposta informativa (geração automática ainda limitada) |

### Auto cut cortes (`/api/auto-cut-cortes/`)

| Método | Sufixo | Descrição |
|--------|--------|------------|
| `POST` | `.../{id}/schedule/` | Agenda um corte finalizado |

---

## Erros e códigos

- **`401` / `403`:** não autenticado ou sem permissão sobre o recurso.  
- **`400`:** validação (campos em falta, estado inválido para a acção).  
- **`404`:** recurso inexistente ou ficheiro em falta no disco.  
- **`500` / **`503`:** erro interno ou serviço indisponível (ex.: OAuth não configurado no `.env`).

Corpos de erro costumam incluir chave `"error"` com mensagem legível.

---

## Evolução: documentação máquina-legível

Para **OpenAPI 3** (Swagger UI / Redoc) gerado a partir dos serializers, o passo típico em Django REST Framework é acrescentar **`drf-spectacular`** (ou equivalente) e expor `/api/schema/`. Não está ligado no repositório para manter dependências mínimas; pode ser uma melhoria futura quando a equipa quiser contratos versionados para clientes externos.

---

## Documentação relacionada

- [ARCHITECTURE.md](ARCHITECTURE.md) — visão de sistema e fluxos  
- [README principal](../README.md) — variáveis de ambiente e execução local  
