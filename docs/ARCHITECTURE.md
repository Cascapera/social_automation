# Arquitetura — Social Automation

Visão de alto nível para onboarding e revisão técnica. Detalhe de negócio permanece nos modelos e no código.

## Visão em camadas

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite) — dashboard, chamadas REST + JWT   │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS
┌────────────────────────────▼────────────────────────────────┐
│  Django — REST API (/api), admin, URLs de callback OAuth      │
│  Autenticação: SimpleJWT + sessão onde aplicável             │
└──────────────┬──────────────────────────────┬──────────────┘
               │                                │
     ┌─────────▼─────────┐            ┌─────────▼─────────┐
     │  PostgreSQL /     │            │  Redis (broker)  │
     │  SQLite (dev)     │            │  + django-celery │
     └───────────────────┘            │    -results      │
                                      └─────────┬─────────┘
                                                │
                    ┌───────────────────────────┴───────────────────────────┐
                    │  Celery workers (filas dedicadas — ver ADR-0001)       │
                    │  processing: FFmpeg, Whisper, auto cuts, jobs          │
                    │  publish: agendamentos, post APIs, reconciliação YT      │
                    └─────────────────────────────────────────────────────────┘
```

**Armazenamento de ficheiros:** `MEDIA_ROOT` (ex.: vídeos exportados, cortes, thumbnails). Em Docker costuma ser volume montado.

## Fluxo principal (happy path)

1. **Configuração:** utilizador regista-se via API, cria *Factory*, *Brands*, credenciais YouTube / canais de busca.
2. **Ingestão:** vídeo entra por upload, URL ou *auto-fetch* (yt-dlp + políticas de idade/views no modelo).
3. **Processamento pesado (fila `processing`):**
   - *Jobs* manuais: cortes, concatenação, legendas (Whisper + queima FFmpeg).
   - *Auto cuts:* transcrição, análise LLM, sugestões, render de cortes.
4. **Inventário e agendamento:** cortes prontos entram no modelo de inventário; *scheduler* diário (ex. 19h) e *beat* geram/atualizam `ScheduledPost`.
5. **Publicação (fila `publish`):** verificação de posts agendados, upload para YouTube / Upload-Post, reconciliação com a API do YouTube.

O **Celery Beat** (`config/celery.py`) dispara tarefas periódicas: check de posts, reconciliação YouTube, geração diária de agendas, *auto-fetch* em intervalos definidos.

## Apps Django (responsabilidade)

| App | Papel |
|-----|--------|
| `apps.api` | ViewSets REST, serializers, contrato HTTP |
| `apps.brands` | Factory, Brand, ativos, OAuth por marca |
| `apps.mediahub` | `SourceVideo` (origens de edição) |
| `apps.cuts` | Cortes derivados do source |
| `apps.jobs` | Jobs de edição, outputs, agendamentos, inventário |
| `apps.auto_cuts` | Pipeline IA: análise, sugestões, cortes automáticos |
| `apps.social` | Tasks de publicação, integrações YouTube, OAuth helpers |

## O que ficou de fora deste documento

- Detalhe de cada *endpoint* HTTP — ver **[API.md](API.md)** (rotas, JWT, acções customizadas). OpenAPI/Swagger pode ser adicionado depois (ex. `drf-spectacular`).
- Políticas exatas de *retry*, limites de API e segredos — variáveis em `.env.example` e código das tasks.

## ADRs (Architecture Decision Records)

Decisões estáveis e discutíveis ficam em [`docs/adr/`](adr/). Começar por:

- [0001 — Filas Celery `processing` vs `publish`](adr/0001-celery-filas-processing-vs-publish.md)
