# 🎬 Social Automation — Plataforma de Automação de Conteúdo Viral

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.2+-092E20?logo=django&logoColor=white)](https://djangoproject.com)
[![Celery](https://img.shields.io/badge/Celery-5.3+-37814A?logo=celery&logoColor=white)](https://celeryproject.org)
[![React](https://img.shields.io/badge/React-18+-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-336791?logo=postgresql&logoColor=white)](https://postgresql.org)

> **Sistema enterprise de produção e distribuição automatizada de vídeos para redes sociais** — da transcrição com IA até a publicação agendada em YouTube, TikTok, Instagram e X.

---

## 📌 Destaques para Gestores Técnicos

| Aspecto | Implementação |
|---------|---------------|
| **Arquitetura** | Monolito modular Django + Celery com filas dedicadas (processing × publish) |
| **Escalabilidade** | Workers Celery separados, suporte a GPU (NVENC, Whisper), PostgreSQL com connection pooling |
| **IA/ML** | Whisper (transcrição), Grok/OpenAI (análise viral), prompts otimizados para CTR |
| **Integrações** | YouTube Data API v3, OAuth2 multi-conta, yt-dlp para ingestão |
| **DevOps** | Docker Compose production-ready, healthchecks, migrações automatizadas |
| **Segurança** | JWT (SimpleJWT), CORS configurado, credenciais por brand, variáveis de ambiente |

---

## 🎯 O que este projeto resolve

Produção de conteúdo em escala para **factories de vídeos** — empresas que operam múltiplos canais (YouTube, Shorts, TikTok, Instagram, X) a partir de um único fluxo:

1. **Ingestão** — Upload, URL do YouTube ou busca automática em canais configurados  
2. **Transcrição** — Whisper (faster-whisper) com suporte a GPU  
3. **Análise IA** — LLM identifica trechos virais, sugere títulos, hooks e thumbnails  
4. **Edição** — Pipeline FFmpeg: cortes, reenquadramento 9:16, intro/outro, legendas queimadas  
5. **Agendamento** — Scheduler diário por factory, slots por brand, timezone-aware  
6. **Publicação** — YouTube (Shorts + Longos), TikTok, Instagram, X via APIs e OAuth  

---

## 🏗️ Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FRONTEND (React + Vite)                            │
│                    Dashboard • Jobs • Auto Cuts • Brands                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BACKEND (Django REST + JWT)                                │
│  /api/*  •  /admin/  •  /social/youtube/  •  Media • Migrations              │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
          ┌─────────────────────────────┼─────────────────────────────┐
          ▼                             ▼                             ▼
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│  CELERY WORKER   │         │ CELERY WORKER    │         │   CELERY BEAT     │
│  (processing)    │         │ (publish)        │         │   (scheduler)     │
│  • Jobs FFmpeg   │         │ • Check posts    │         │ • 19h: agenda     │
│  • Auto Cuts     │         │ • Upload YT     │         │ • 15min: fetch    │
│  • Whisper       │         │ • Reconcile     │         │ • 1min: posts      │
│  • Subtitles     │         │ • Thumbnails     │         │ • 4h: cleanup     │
└────────┬─────────┘         └────────┬─────────┘         └────────┬─────────┘
         │                            │                            │
         └────────────────────────────┼────────────────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Redis (broker)  │  PostgreSQL  │  Storage (media)  │  FFmpeg  │  APIs       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Stack Tecnológica

| Camada | Tecnologias |
|--------|-------------|
| **Backend** | Django 5.2, Django REST Framework, SimpleJWT, django-celery-results |
| **Filas** | Celery 5.3, Redis 7 |
| **Banco** | PostgreSQL 16 (produção) / SQLite (dev) |
| **IA** | faster-whisper, OpenAI API, Grok (xAI) |
| **Mídia** | FFmpeg, yt-dlp, Pillow |
| **Integrações** | Google API (YouTube Data v3), OAuth2 |
| **Frontend** | React 18, Vite 5, React Router |
| **Infra** | Docker, Docker Compose |

---

## 🚀 Funcionalidades Principais

### Auto Cuts (IA)
- Transcrição automática com **Whisper** (CPU/GPU)
- Análise de viralidade com **LLM** (Grok/OpenAI)
- Sugestão de cortes curtos (Shorts) e longos (YouTube)
- Thumbnails automáticas com fontes e cores customizáveis
- Modos: viral, **viral longo** (shorts 90–160s), educacional, PT, EN, tradução EN→PT
- Reenquadramento vertical: zoom/crop ou frame centralizado

### Jobs de Edição
- Pipeline FFmpeg: cortes, concatenação, transições (fade, wipe, dissolve)
- Intro/outro por brand
- Legendas queimadas (Whisper + estilização)
- Suporte a **NVENC** (aceleração GPU)
- Export para múltiplas plataformas

### Factory & Brands
- **Factory**: unidade de produção com timezone, horários de agendamento
- **Brands**: canais por tema (Negócios, Psicologia, Histórias, Polêmicas, Comédia)
- **Auto-fetch**: busca automática em canais YouTube configurados
- Políticas: idade mínima do vídeo, views mínimas, deduplicação

### Publicação
- Agendamento diário automático (19h)
- YouTube Shorts + Longos, TikTok, Instagram, X
- OAuth por brand/canal
- Reconcilição com YouTube (status real dos vídeos)
- Retry com backoff, deduplicação por fingerprint

---

## ⚙️ Pré-requisitos

- **Python 3.11+**
- **FFmpeg** (com suporte a NVENC opcional para GPU)
- **Redis** (para Celery)
- **PostgreSQL** (opcional; SQLite para dev)
- **Node.js 18+** (para o frontend)

---

## 🛠️ Instalação Rápida

### 1. Clone e ambiente virtual

```bash
git clone <repo-url>
cd social_automation
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 2. Variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto e configure:

```env
DJANGO_SECRET_KEY=sua-chave-secreta
DJANGO_DEBUG=1
DATABASE_URL=                    # vazio = SQLite
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=django-db
OPENAI_API_KEY=sk-...            # para transcrição/análise
XAI_API_KEY=xai-...              # Grok (opcional)
# YouTube OAuth: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI
```

### 3. Banco e migrações

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Subir com Docker (recomendado)

```bash
docker compose up -d
# Acesse: http://localhost:8000/admin
```

### 5. Frontend (opcional)

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173
```

---

## 📁 Estrutura do Projeto

```
social_automation/
├── apps/
│   ├── api/              # REST API
│   ├── auto_cuts/        # IA: transcrição, análise, sugestões
│   ├── brands/           # Factory, Brand, SearchChannel, OAuth
│   ├── cuts/             # Cortes manuais
│   ├── jobs/             # Pipeline de edição, ScheduledPost
│   ├── mediahub/         # SourceVideo
│   └── social/           # Publicação, YouTube, tasks de agendamento
├── config/               # Celery app e beat_schedule
├── social_automation/    # Settings, URLs
├── frontend/             # React + Vite
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## 🔧 Comandos Úteis

| Comando | Descrição |
|---------|-----------|
| `python manage.py runserver` | Inicia o servidor Django |
| `start_celery.bat` | Worker de processamento (jobs, auto cuts) |
| `start_celery_publish.bat` | Worker de publicação (YouTube, Upload Post) |
| `start_celery_beat.bat` | Scheduler (agendamentos) |
| `celery -A config worker -l INFO -Q processing` | Worker processing (Linux) |
| `celery -A config worker -l INFO -Q publish` | Worker publish (Linux) |
| `celery -A config beat -l INFO` | Scheduler (agendamentos) |
| `python manage.py run_scheduled_posts_now` | Força publicação imediata |
| `python manage.py fix_youtube_posted_status` | Reconcilia status no YouTube |

---

## 📐 Decisões de Arquitetura

- **Filas separadas**: `processing` (pesado) e `publish` (leve) — evita que transcrição/render bloqueie agendamentos
- **Factory/Brand**: modelo multi-tenant por unidade de negócio
- **OAuth por brand**: credenciais isoladas por canal, fallback em ordem
- **Deduplicação**: `ProcessedYoutubeVideo` e `upload_fingerprint` evitam reprocessamento e reenvio
- **Idempotência**: `FactoryScheduleRun` por data evita duplicar agendamentos diários

---

## 📄 Licença

Projeto privado. Entre em contato para uso comercial.

---

## 👤 Autor

Desenvolvido com foco em **escalabilidade**, **manutenibilidade** e **boas práticas** de engenharia de software.

---

*README otimizado para recrutadores e gestores técnicos — demonstra domínio em backend Python, arquitetura distribuída, IA aplicada e integrações de APIs.*
