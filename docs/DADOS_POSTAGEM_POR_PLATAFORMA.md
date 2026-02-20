# Dados ajustáveis na postagem por plataforma

Resumo dos campos que podemos configurar em cada rede ao publicar um vídeo.

---

## Visão geral comparativa

| Campo | Instagram Reels | TikTok | YouTube Shorts | YouTube (longo) |
|-------|-----------------|--------|----------------|----------------|
| **Título** | ❌ | ✅ (caption) | ✅ | ✅ |
| **Descrição** | ❌ | ❌ (usa caption) | ✅ | ✅ |
| **Caption/Legenda** | ✅ | ✅ (title = caption) | — | — |
| **Tags/Hashtags** | (no caption) | (no caption) | ✅ | ✅ |
| **Capa/Thumbnail** | ✅ (cover_url) | ✅ (timestamp) | ✅ | ✅ |
| **Privacidade** | ❌ (público) | ✅ | ✅ | ✅ |
| **Localização** | ✅ (location_id) | ❌ | ❌ | ❌ |
| **Duet/Stitch** | — | ✅ (disable) | — | — |
| **Comentários** | — | ✅ (disable) | — | ✅ |
| **Categoria** | — | — | ✅ | ✅ |
| **Agendar publicação** | ❌ | ❌ | ✅ (publishAt) | ✅ |
| **Feito para crianças** | — | — | ✅ | ✅ |
| **Conteúdo pago** | — | ✅ (brand_content) | — | — |

---

## Instagram Reels (IG)

| Campo | Tipo | Obrigatório | Limite | Observação |
|-------|------|-------------|--------|------------|
| **caption** | string | Não | — | Legenda do Reel. Hashtags e @ no texto |
| **cover_url** | URL | Não | — | URL pública da imagem de capa (Reels tab) |
| **location_id** | string | Não | — | ID do local (Facebook Place) |
| **media_type** | enum | Sim | — | "REELS" |
| **video_url** | URL | Sim | — | URL pública do vídeo (MP4) |

**Não disponível**: título separado, descrição, tags, privacidade (via API costuma ser público), agendamento.

---

## TikTok (TT)

| Campo | Tipo | Obrigatório | Limite | Observação |
|-------|------|-------------|--------|------------|
| **title** | string | Não | 2200 chars (UTF-16) | Legenda. Hashtags # e @ são detectados |
| **privacy_level** | enum | Sim | — | PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR, SELF_ONLY |
| **video_cover_timestamp_ms** | int | Não | — | Frame (em ms) usado como capa |
| **disable_duet** | bool | Não | — | Bloquear Duets |
| **disable_stitch** | bool | Não | — | Bloquear Stitches |
| **disable_comment** | bool | Não | — | Bloquear comentários |
| **brand_content_toggle** | bool | Sim | — | true = parceria paga |
| **brand_organic_toggle** | bool | Não | — | true = promovendo negócio próprio |
| **is_aigc** | bool | Não | — | true = conteúdo gerado por IA |

**Não disponível**: descrição separada (tudo vai no title), tags separadas, categoria, agendamento.

---

## YouTube Shorts (YT) e YouTube (YTB)

Ambos usam a mesma API (`videos.insert`). Shorts = vídeo vertical ≤ 60s.

### snippet (metadados principais)

| Campo | Tipo | Obrigatório | Limite | Observação |
|-------|------|-------------|--------|------------|
| **title** | string | Sim | 100 chars | Título do vídeo |
| **description** | string | Não | 5000 chars | Descrição |
| **tags** | array | Não | 500 chars total | Lista de tags |
| **categoryId** | string | Sim | — | Ex: "22" (People & Blogs), "24" (Entertainment) |
| **defaultLanguage** | string | Não | — | Código do idioma (ex: "pt") |
| **defaultAudioLanguage** | string | Não | — | Idioma do áudio |

### status

| Campo | Tipo | Obrigatório | Observação |
|-------|------|-------------|------------|
| **privacyStatus** | enum | Sim | public, private, unlisted |
| **publishAt** | datetime | Não | Agendar publicação (só com privacyStatus=private) |
| **embeddable** | bool | Não | Permitir embed |
| **publicStatsViewable** | bool | Não | Estatísticas públicas |
| **madeForKids** | bool | Sim | Conteúdo para crianças |
| **selfDeclaredMadeForKids** | bool | Não | Auto-declaração |

### contentDetails (opcional)

| Campo | Tipo | Observação |
|-------|------|------------|
| **caption** | bool | Legendas disponíveis |
| **contentRating** | object | Classificação etária por país |

### Thumbnail

- Upload separado via `thumbnails.set` após o vídeo
- Ou YouTube gera automaticamente se não enviar

---

## Proposta de modelo unificado no sistema

Para o usuário editar uma vez e aplicar em todas as redes (com mapeamento):

```python
# Campos comuns (ScheduledPost ou Job)
caption = "Legenda principal"           # → IG caption, TT title, YT description (ou parte)
title = "Título do vídeo"               # → YT title, TT title (se quiser diferenciar)
description = "Descrição longa"          # → YT description
tags = ["tag1", "tag2"]                 # → YT tags, hashtags no caption para IG/TT
hashtags = "#fyp #viral"                # → IG/TT (no caption)

# Por plataforma (override ou específico)
cover_timestamp_ms = 1000               # TT: frame da capa
cover_image_url = "https://..."         # IG: URL da capa
privacy = "public"                      # TT, YT (IG costuma ser público)
disable_comments = False                # TT
disable_duet = False                    # TT
disable_stitch = False                  # TT
category_id = "22"                      # YT
made_for_kids = False                   # YT
publish_at = "2025-02-20T14:00:00Z"    # YT (agendar)
location_id = None                      # IG
brand_content = False                   # TT (parceria paga)
```

### Estratégia de mapeamento

1. **Caption única** → IG caption, TT title (com hashtags), início da YT description
2. **Title** → YT title; TT pode usar title ou caption
3. **Description** → YT description (pode incluir caption + texto extra)
4. **Tags** → YT tags; para IG/TT, usuário coloca hashtags no caption
5. **Campos específicos** → formulário com abas ou seções por plataforma

---

## Recomendação para o frontend

**Opção A – Formulário unificado simples**
- Caption (obrigatório)
- Título (para YT)
- Descrição (para YT, opcional)
- Hashtags (campo separado, concatena no caption para IG/TT)
- Privacidade (quando aplicável)
- Checkboxes: desativar comentários, Duet, Stitch (TikTok)

**Opção B – Formulário com abas por plataforma**
- Aba "Geral": caption, título, descrição
- Aba "Instagram": capa, localização
- Aba "TikTok": privacidade, capa (timestamp), Duet/Stitch/comentários
- Aba "YouTube": tags, categoria, privacidade, feito para crianças, agendar

**Opção C – Híbrido**
- Campos comuns no topo (caption, title, description, tags)
- Seção "Opções por rede" expansível com overrides específicos

Sugestão: começar com **Opção A** e evoluir para **Opção C** conforme necessidade.
