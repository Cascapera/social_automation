# Plano: Integração com Redes Sociais e Agendamento

## Situação atual

- **ScheduledPost** já existe: armazena job, plataformas (IG, TT, YT, YTB), `scheduled_at`, status
- **API** permite criar agendamentos via `POST /api/scheduled-posts/`
- **Frontend** tem fluxo de agendamento (Agendamento.jsx, NovoVideo.jsx, Dashboard.jsx)
- **Falta**: não há task Celery que execute a postagem; os agendamentos ficam apenas no banco

---

## Arquitetura proposta

### 1. Camadas

```
┌─────────────────────────────────────────────────────────────────┐
│  Celery Beat (a cada 1 min)                                     │
│  → Task: check_scheduled_posts_task                              │
│  → Busca ScheduledPost com status=PENDING e scheduled_at <= now   │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Task: post_to_platforms_task(scheduled_post_id)                 │
│  → Para cada plataforma em scheduled_post.platforms               │
│  → Chama o publisher correspondente (IG, TT, YT, YTB)            │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Publishers (apps/social/publishers/)                            │
│  - InstagramPublisher (Reels)                                    │
│  - TikTokPublisher                                               │
│  - YouTubeShortsPublisher                                        │
│  - YouTubePublisher (vídeos longos)                              │
└─────────────────────────────────────────────────────────────────┘
```

### 2. Armazenamento de credenciais

**Opção A – Por Brand (recomendado)**  
Cada marca tem suas contas conectadas. Um job pertence a uma brand → usa as credenciais da brand.

- Novo modelo: `BrandSocialAccount` (brand, platform, access_token, refresh_token, expires_at, extra_data)
- Fluxo: usuário conecta conta no painel da marca → OAuth → salva tokens

**Opção B – Por usuário**  
Cada usuário conecta suas contas. Jobs do usuário usam suas credenciais.

- Modelo: `UserSocialAccount` (user, platform, tokens...)
- Mais simples se for uso pessoal; menos flexível para múltiplas marcas

**Recomendação**: Opção A (por Brand), pois o sistema já é multi-marca.

---

## Requisitos por plataforma

### Instagram Reels (IG)

| Item | Detalhe |
|------|---------|
| **API** | Instagram Graph API (Meta) |
| **Requisitos** | Conta Business/Creator, Meta App, permissões aprovadas |
| **OAuth** | Facebook Login for Business |
| **Permissões** | `instagram_content_publish`, `instagram_business_basic` |
| **Upload** | URL pública do vídeo OU resumable upload |
| **Limites** | ~30 posts/dia via API |
| **Complexidade** | Alta (aprovação Meta, Business account) |

### TikTok (TT)

| Item | Detalhe |
|------|---------|
| **API** | TikTok Content Posting API |
| **Requisitos** | TikTok for Developers, app aprovado |
| **OAuth** | TikTok Login Kit |
| **Upload** | Direct upload ou URL |
| **Complexidade** | Alta (aprovação TikTok, documentação em evolução) |

### YouTube Shorts (YT)

| Item | Detalhe |
|------|---------|
| **API** | YouTube Data API v3 |
| **Requisitos** | Google Cloud Project, OAuth 2.0 |
| **OAuth** | Google OAuth (Installed App ou Web) |
| **Upload** | `videos.insert` com `MediaFileUpload` |
| **Shorts** | Mesmo endpoint; Shorts = vídeo vertical ≤ 60s |
| **Complexidade** | Média (bem documentado) |

### YouTube (YTB) – vídeos longos

| Item | Detalhe |
|------|---------|
| **API** | YouTube Data API v3 (mesmo do Shorts) |
| **Diferença** | Sem limite de 60s; metadata diferente |
| **Complexidade** | Média |

---

## Estratégia de implementação

### Fase 1 – Infraestrutura (base)

1. **Celery Beat**
   - Configurar `CELERY_BEAT_SCHEDULE` para rodar `check_scheduled_posts_task` a cada 1 min
   - Task busca `ScheduledPost` com `status=PENDING` e `scheduled_at <= now`
   - Para cada um: enfileira `post_to_platforms_task`

2. **Modelo de credenciais**
   - `BrandSocialAccount`: brand, platform (IG/TT/YT/YTB), tokens, expires_at
   - Migrations

3. **Estrutura de publishers**
   - `apps/social/` (novo app)
   - Interface base: `publish(job, account, caption?) -> result`
   - Um publisher por plataforma (stub inicial)

### Fase 2 – YouTube primeiro

- API mais estável e documentada
- OAuth com `google-auth-oauthlib` e `google-api-python-client`
- Tela de “Conectar YouTube” na marca
- Publisher real para YT e YTB

### Fase 3 – Instagram

- Meta for Developers: criar app, configurar Instagram API
- OAuth com `requests` ou lib específica
- Publisher para Reels
- Atenção: vídeo precisa estar em URL pública ou usar resumable upload

### Fase 4 – TikTok

- TikTok for Developers
- Publisher quando a API estiver estável
- Pode ser adiado se a API for muito restritiva

---

## Fluxo de vídeo para upload

1. **URL pública**  
   - Servir o vídeo em URL acessível (ex.: `https://seudominio.com/media/exports/job_X.mp4`)  
   - Instagram aceita URL; YouTube faz upload do arquivo

2. **Upload direto**  
   - YouTube: ler arquivo do disco e enviar via `MediaFileUpload`  
   - Instagram: resumable upload ou URL  
   - TikTok: conforme documentação

3. **Caption**  
   - Adicionar campo `caption` (ou `description`) em `Job` ou `ScheduledPost`  
   - Cada plataforma pode ter regras diferentes (hashtags, limites de caracteres)

---

## Modelo de dados sugerido

```python
# BrandSocialAccount (novo)
class BrandSocialAccount(models.Model):
    PLATFORM = [("IG", "Instagram"), ("TT", "TikTok"), ("YT", "YouTube Shorts"), ("YTB", "YouTube")]
    brand = models.ForeignKey(Brand, ...)
    platform = models.CharField(choices=PLATFORM)
    account_id = models.CharField()      # ID na plataforma
    account_name = models.CharField()    # Nome exibido
    access_token = models.TextField()    # Criptografado em produção
    refresh_token = models.TextField(null=True)
    token_expires_at = models.DateTimeField(null=True)
    extra_data = models.JSONField(default=dict)  # platform-specific
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

```python
# ScheduledPost (ajustes)
# Adicionar: caption, per_social_account (FK opcional para escolher qual conta)
```

---

## Ordem sugerida de implementação

| # | Tarefa | Esforço |
|---|--------|---------|
| 1 | Celery Beat + `check_scheduled_posts_task` | Baixo |
| 2 | Modelo `BrandSocialAccount` + migrations | Baixo |
| 3 | API: conectar/desconectar conta (OAuth flow) | Médio |
| 4 | Publisher YouTube (YT + YTB) | Médio |
| 5 | Frontend: tela “Contas conectadas” por marca | Médio |
| 6 | Campo caption em Job/ScheduledPost | Baixo |
| 7 | Publisher Instagram | Alto |
| 8 | Publisher TikTok | Alto |

---

## Riscos e alternativas

| Risco | Mitigação |
|-------|-----------|
| Aprovação demorada (Meta, TikTok) | Começar por YouTube; IG/TT em paralelo |
| Tokens expiram | Refresh automático; notificar usuário se falhar |
| Rate limits | Fila com backoff; respeitar limites por plataforma |
| Vídeo não acessível (URL) | Garantir HTTPS, CORS e URL pública |
| Múltiplas contas por plataforma | `BrandSocialAccount` permite várias; usuário escolhe no agendamento |

---

## Próximo passo

Definir se a implementação começa por:

1. **YouTube** (mais simples, documentação boa)  
2. **Instagram** (mais demanda, mas mais burocrático)  
3. **Ambos em paralelo** (YouTube primeiro, Instagram em seguida)

Sugestão: começar pela **Fase 1 + YouTube** para ter um fluxo completo funcionando e depois adicionar Instagram e TikTok.
