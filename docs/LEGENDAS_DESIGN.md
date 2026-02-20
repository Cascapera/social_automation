# Design: Sistema de Legendas com Whisper

## Visão geral

Fluxo: Gerar transcrição (Whisper) → Usuário edita texto → Queimar legendas no vídeo.

---

## 1. Modelo de dados

### Opção A: Campos no Job (mais simples)

```python
# Job model - adicionar:
subtitle_status = CharField(null=True, blank=True)  
# null = sem legenda
# "generating" = Whisper rodando
# "ready_for_edit" = pronta para edição
# "approved" = usuário confirmou (opcional)
# "burning" = queimando no vídeo
# "burned" = concluído

subtitle_segments = JSONField(null=True, blank=True)
# [{ "start": 0.0, "end": 2.5, "text": "Olá pessoal" }, ...]

subtitle_style = JSONField(null=True, blank=True)
# { "font": "Arial", "size": 24, "color": "#FFFFFF", "outline_color": "#000000", "position": "bottom" }
# Valores padrão se null
```

### Opção B: Modelo separado JobSubtitle

```python
class JobSubtitle(models.Model):
    job = OneToOneField(Job, on_delete=CASCADE)
    status = CharField()  # generating, ready_for_edit, burning, burned
    segments = JSONField()  # [{start, end, text}, ...]
    created_at = DateTimeField()
    updated_at = DateTimeField()
```

**Recomendação:** Opção A (campos no Job) para MVP.

---

## 2. Endpoints da API

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `POST` | `/api/jobs/{id}/generate-subtitles/` | Inicia geração de legendas. Enfileira tarefa Celery. Retorna `{ "status": "generating" }` |
| `GET` | `/api/jobs/{id}/` | Incluir `subtitle_status` e `subtitle_segments` na resposta |
| `PATCH` | `/api/jobs/{id}/subtitles/` | Atualiza segmentos e/ou estilo. Body: `{ "segments": [...], "style": {...} }` |
| `POST` | `/api/jobs/{id}/burn-subtitles/` | Queima legendas no vídeo. Enfileira tarefa Celery. Retorna `{ "status": "burning" }` |
| `GET` | `/api/jobs/{id}/download-srt/` | (Opcional) Baixa arquivo SRT para preview/download |

---

## 3. Tarefas Celery

### Task 1: `generate_subtitles_task(job_id)`

1. Busca job e arquivo de saída (RenderOutput)
2. Extrai áudio do vídeo (ou passa vídeo direto para Whisper)
3. Roda faster-whisper (modelo medium)
4. Converte resultado em segmentos `[{start, end, text}, ...]`
5. Salva em `job.subtitle_segments`, `job.subtitle_status = "ready_for_edit"`
6. Em erro: `job.subtitle_status = "error"`, `job.subtitle_error = "..."`

### Task 2: `burn_subtitles_task(job_id)`

1. Busca job e `subtitle_segments`
2. Gera arquivo SRT temporário
3. FFmpeg: `-vf "subtitles=arquivo.srt"` para queimar no vídeo
4. Substitui arquivo em RenderOutput (ou cria novo e atualiza)
5. `job.subtitle_status = "burned"`
6. Remove SRT temporário

---

## 4. Fluxo de telas (frontend)

### 4.1 Página: Editar Vídeos (seção Vídeos finalizados)

**Card de job finalizado (status DONE):**

| Estado `subtitle_status` | Botões/UI |
|--------------------------|-----------|
| `null` ou vazio | Botão **"Gerar legenda"** |
| `generating` | Texto "Gerando legendas..." + spinner |
| `ready_for_edit` | Botão **"Editar legendas"** |
| `burning` | Texto "Queimando legendas..." + spinner |
| `burned` | Badge "Com legendas" (opcional) ou sem botão |
| `error` | Mensagem de erro + botão "Tentar novamente" |

### 4.2 Modal/Página: Editor de Legendas

**Acesso:** Clique em "Editar legendas" no card do job.

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Editar legendas - [Nome do Job]                    [X]  │
├─────────────────────────────────────────────────────────┤
│  ▼ Estilo das legendas                                  │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Fonte: [Arial        ▼]  Tamanho: [24    ]       │   │
│  │ Cor do texto: [■ #FFFFFF]  Borda: [■ #000000]    │   │
│  │ Posição: [Inferior ▼]                            │   │
│  └─────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  Segmento 1   [0:00 → 0:03]                              │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Olá pessoal, bem-vindos ao canal                 │   │
│  └─────────────────────────────────────────────────┘   │
│  ...                                                    │
├─────────────────────────────────────────────────────────┤
│  [Cancelar]  [Salvar alterações]  [Queimar no vídeo]    │
└─────────────────────────────────────────────────────────┘
```

**Comportamento:**
- Cada segmento: timestamp (read-only) + textarea editável
- "Salvar alterações" → PATCH `/api/jobs/{id}/subtitles/` com segmentos editados
- "Queimar no vídeo" → POST `/api/jobs/{id}/burn-subtitles/` → fecha modal, mostra "Queimando..."
- Polling no job para atualizar status (igual ao processamento)

### 4.3 Alternativa: Página dedicada

**Rota:** `/editar-videos/legendas/:jobId`

- Mesmo conteúdo do modal, em página cheia
- Útil para muitos segmentos

**Recomendação:** Modal primeiro; página dedicada se o modal ficar pesado.

---

## 5. Fluxo sequencial completo

```
1. Job DONE, output_url existe
   └─ Usuário clica "Gerar legenda"

2. POST /jobs/{id}/generate-subtitles/
   └─ Celery: generate_subtitles_task(job_id)
   └─ Frontend: polling job.subtitle_status

3. subtitle_status = "ready_for_edit"
   └─ Botão "Editar legendas" aparece

4. Usuário clica "Editar legendas"
   └─ Modal abre com segmentos
   └─ Usuário edita textos

5. Usuário clica "Salvar alterações"
   └─ PATCH /jobs/{id}/subtitles/ { segments: [...] }

6. Usuário clica "Queimar no vídeo"
   └─ POST /jobs/{id}/burn-subtitles/
   └─ Celery: burn_subtitles_task(job_id)
   └─ Frontend: polling job.subtitle_status

7. subtitle_status = "burned"
   └─ Vídeo atualizado com legendas
   └─ Download já retorna vídeo legendado
```

---

## 6. Dependências

```
# requirements.txt
faster-whisper>=1.0.0
```

**CUDA:** faster-whisper usa PyTorch com CUDA. RTX 3060 precisa de `pip install faster-whisper` (já puxa dependências). Verificar se `torch` com CUDA está instalado.

---

## 7. Ordem de implementação sugerida

1. **Backend:** Modelo (campos no Job) + migrations
2. **Backend:** Task `generate_subtitles_task` + endpoint `generate-subtitles/`
3. **Backend:** Task `burn_subtitles_task` + endpoint `burn-subtitles/` + `PATCH subtitles/`
4. **Backend:** Incluir `subtitle_status` e `subtitle_segments` no JobSerializer
5. **Frontend:** Botões na seção "Vídeos finalizados"
6. **Frontend:** Modal editor de legendas
7. **Frontend:** Polling e estados de loading
8. **Testes:** Fluxo completo

---

## 8. Estilo das legendas (editável)

### Parâmetros suportados pelo FFmpeg (force_style)

| Parâmetro | Exemplo | Descrição |
|-----------|---------|-----------|
| FontName | Arial, Helvetica | Nome da fonte |
| FontSize | 18, 24, 32 | Tamanho em px |
| PrimaryColour | &H00FFFFFF | Cor do texto (BGR em hex) |
| OutlineColour | &H00000000 | Cor da borda/contorno |
| BorderStyle | 1 | 1 = outline + shadow |
| Outline | 2 | Espessura da borda |
| Shadow | 1 | Profundidade da sombra |
| Alignment | 2 | 1=inferior, 2=centro, 3=superior |
| MarginV | 20 | Margem vertical (px da borda) |

### Interface de edição

- **Fonte:** Dropdown (Arial, Helvetica, Open Sans, Roboto, etc.)
- **Tamanho:** Input numérico (16–48)
- **Cor do texto:** Color picker (hex)
- **Cor da borda:** Color picker (para contorno legível)
- **Posição:** Inferior / Centro / Superior (útil para vertical 9:16)

### Valores padrão

```json
{
  "font": "Arial",
  "size": 24,
  "color": "#FFFFFF",
  "outline_color": "#000000",
  "outline": 2,
  "position": "bottom"
}
```

### Conversão para FFmpeg

- Cor hex `#FFFFFF` → ASS usa `&H00BBGGRR` (BGR invertido): `#FFFFFF` → `&H00FFFFFF`
- `position`: bottom=2, center=5, top=8 (valores Alignment do ASS)

---

## 9. Considerações

- **Idioma:** Detectar automaticamente ou permitir escolha (ex.: `language="pt"` no Whisper)
- **Vídeo vertical:** Ajustar posição das legendas no FFmpeg para 9:16
