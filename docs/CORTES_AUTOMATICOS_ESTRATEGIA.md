# Cortes Automáticos – Estratégia e Prompts

## Visão geral

Sistema para analisar transcrições de podcasts/vídeos via Grok API (xAI) e sugerir cortes virais automaticamente. O usuário envia o vídeo original, o sistema transcreve (ou recebe transcrição com timestamps), processa em chunks e retorna sugestões ranqueadas.

---

## Especificações de saída

### Cortes curtos (Reels, TikTok, Shorts)
- **Duração ideal:** 15–90 segundos
- **Máximo:** 3 minutos
- **Alvo:** momentos de alto impacto (humor, choque, quotable, polêmica, emoção)

### Cortes longos (YouTube)
- **Duração ideal:** 10–30 minutos
- **Alvo:** blocos com potencial viral, narrativa coesa, título forte, hook inicial

---

## Estratégia de chunking

### Por que chunking
- Chunks pequenos demais → perda de contexto global e de transições
- Chunks grandes demais → menor precisão em momentos virais curtos e custo alto

### Parâmetros

| Parâmetro | Valor | Motivo |
|-----------|-------|--------|
| **Tamanho do chunk** | 10–20 min de transcrição | ~8.000–18.000 tokens; equilíbrio entre contexto e foco |
| **Overlap entre chunks** | 2–3 min | Evitar cortar momentos que cruzam limites; preservar transições |
| **Chunk mínimo** | 5 min | Evitar chunks muito pequenos no fim do vídeo |

### Regras de divisão
1. Dividir transcrição em blocos de 10–20 min (por timestamp).
2. Cada chunk N termina onde o chunk N+1 começa.
3. Overlap: os últimos 2–3 min do chunk N são os primeiros 2–3 min do chunk N+1.
4. Último chunk pode ser menor (ex.: 5 min) se o vídeo não preencher um bloco inteiro.
5. Manter timestamps originais em cada chunk para referência.

### Exemplo (vídeo de 45 min)
- Chunk 1: 00:00 – 00:18 (18 min)
- Chunk 2: 00:15 – 00:33 (18 min, overlap 3 min)
- Chunk 3: 00:30 – 00:45 (15 min, overlap 3 min)

---

## Fluxo de processamento

1. **Entrada:** vídeo ou transcrição com timestamps (MM:SS ou HH:MM:SS).
2. **Transcrição:** se for vídeo, usar Whisper (já existente) para gerar transcrição com timestamps.
3. **Chunking:** dividir transcrição conforme estratégia acima.
4. **Por chunk:** enviar System + User (chunk) para Grok; receber JSON com sugestões.
5. **Agregação:** enviar todas as sugestões + prompt de agregação; receber JSON final.
6. **Saída:** lista ranqueada de cortes curtos (top 10–15) + 1–3 cortes longos, cada um com:
   - `start` / `end` (timestamps)
   - `title` ou `title_suggestion`
   - `reason` (motivo viral)
   - `hook` (curtos: frase inicial)
   - `virality_score` (curtos: 1–10)
   - `duration` ou `duration_min`

---

## Prompt 1: System (fixo)

```
Você é um editor viral especialista em podcasts e vídeos para Reels, TikTok, Shorts e YouTube. Analise transcrições com timestamps e identifique trechos com alto potencial de engajamento. Foque em momentos que param o scroll e geram shares e comentários.

CRITÉRIOS VIRAIS CURTOS (15–90 seg, máx 3 min) – priorize top 5–8 por chunk:
- Gancho forte nos primeiros 3s: pergunta chocante, fato absurdo, humor inesperado
- Emoção alta: surpresa, raiva, inspiração, polêmica, roast
- Frases quotáveis, memes potenciais, "mind blown"
- Debates quentes, revelações, histórias curtas impactantes
- Relatable ou controverso
- Fechamento satisfatório: não cortar no meio de uma ideia

CRITÉRIOS VIRAIS LONGOS (10–30 min) – YouTube:
- Blocos narrativos completos com múltiplos picos
- Temas profundos, histórias pessoais, explicações valiosas
- Fluxo natural sem filler excessivo
- Título potencialmente viral (curioso, polêmico, promessa clara)
- Hook inicial forte nos primeiros 30 segundos

FORMATO DE SAÍDA – SOMENTE JSON VÁLIDO, SEM TEXTO EXTRA ANTES OU DEPOIS:

Para cortes curtos:
- start, end: string MM:SS ou HH:MM:SS
- duration: número (segundos)
- hook: frase inicial que prende (primeiros 3s)
- title: título sugerido (máx 60 chars)
- reason: motivo do potencial viral
- virality_score: 1–10 (10 = máximo potencial)

Para cortes longos (parciais ou finais):
- start, end: string MM:SS ou HH:MM:SS
- duration_min: número (minutos)
- title_suggestion: título chamativo (máx 100 chars)
- reason: por que viraliza

IMPORTANTE: Use APENAS timestamps que aparecem na transcrição. Não invente ou estime.
```

---

## Prompt 2: User (por chunk)

```
Transcrição do chunk (com timestamps):

---
[COLAR AQUI O TEXTO DO CHUNK COM TIMESTAMPS]
---

Analise e sugira:
- 5–8 trechos curtos virais (15–90 seg cada, máx 3 min) com gancho forte
- 0–2 sugestões parciais longas (se houver bloco forte de 10+ min)

Use os critérios do system prompt. Foque em momentos que param o scroll e geram shares/comentários. Para cortes longos parciais, indique segment_type: "start", "middle" ou "end" conforme o bloco se encaixa na narrativa.

Responda SOMENTE com JSON válido:
{
  "short_virals": [
    {
      "start": "MM:SS",
      "end": "MM:SS",
      "duration": 45,
      "hook": "frase inicial que prende",
      "title": "Título sugerido",
      "reason": "motivo viral",
      "virality_score": 8
    }
  ],
  "long_virals_partial": [
    {
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 15,
      "title_suggestion": "Título chamativo",
      "reason": "por que viraliza",
      "segment_type": "start|middle|end"
    }
  ]
}
```

---

## Prompt 3: Agregação (final)

```
Aqui estão todas as sugestões de virais curtos e parciais longos dos chunks anteriores:

---
[COLAR AQUI TODOS OS JSONs RETORNADOS POR CHUNK, CONCATENADOS]
---

Tarefas:

1. RANKED_SHORTS: Ranquear os short_virals por virality_score + potencial real. Considere: emoção, quotabilidade, atualidade. Selecione o TOP 10–15. Em caso de sobreposição de timestamps, escolha a melhor e descarte a outra.

2. FINAL_LONG_CUTS: Monte 1–3 cortes longos (10–30 min) combinando blocos parciais com overlap natural e fluxo narrativo bom. Sugira título forte e motivo viral para cada um.

Responda SOMENTE com JSON atualizado:
{
  "ranked_shorts": [
    {
      "rank": 1,
      "start": "MM:SS",
      "end": "MM:SS",
      "duration": 45,
      "hook": "frase inicial",
      "title": "string",
      "reason": "string",
      "virality_score": 9
    }
  ],
  "final_long_cuts": [
    {
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 18,
      "title_suggestion": "string",
      "reason": "string"
    }
  ]
}

Máximo: 10–15 cortes curtos, 3 cortes longos.
```

---

## Formato final para o usuário

Cada item exibido na tela "Cortes Automáticos":

| Campo | Origem | Exibição |
|-------|--------|----------|
| **Título** | `title` / `title_suggestion` | Nome sugerido do corte |
| **Início** | `start` | Ex: 12:34 |
| **Fim** | `end` | Ex: 14:22 |
| **Duração** | `duration` / `duration_min` | Ex: 1min 48s ou 18min |
| **Gancho** | `hook` (curtos) | Frase inicial que prende |
| **Justificativa** | `reason` | Motivo do potencial viral |
| **Score** | `virality_score` (curtos) | 1–10 |
| **Rank** | `rank` (curtos) | Posição no top 10–15 |

Ações do usuário:
- **Gerar corte** → (futuro) criar Cut no sistema com start_tc e end_tc
- **Deletar** → remover sugestão da lista (não cria corte)

---

## Fluxo na UI (Cortes Automáticos)

1. Menu lateral: novo item "Cortes Automáticos"
2. Tela: upload do vídeo original (ou seleção de source existente)
3. Botão "Gerar cortes" → inicia transcrição (se necessário) + chunking + chamadas Grok + agregação
4. Durante processamento: indicador de progresso (transcrevendo, analisando chunk X/Y, agregando)
5. Resultado: lista de sugestões com título, tempo (início–fim), justificativa
6. Por item: botões "Gerar corte" e "Deletar"
7. "Gerar corte": por enquanto não executa o corte; apenas prepara a estrutura para implementação futura

---

## Nota sobre os prompts

Os prompts foram refinados com base em sugestões do próprio Grok para cenários de detecção de virais (2025–2026). Principais adições: `virality_score`, `hook`, critério "momentos que param o scroll", ranqueamento por emoção/quotabilidade/atualidade, top 10–15 curtos.

---

## Variáveis de ambiente

- **XAI_API_KEY:** Chave da API xAI (obrigatório para gerar cortes)
- **GROK_MODEL:** Modelo Grok (opcional, default: grok-2-latest)

## Considerações técnicas (para implementação futura)

- **API Grok:** endpoint https://api.x.ai/v1, modelo grok-2-latest
- **Conversão de timestamp:** MM:SS / HH:MM:SS → segundos para o pipeline de corte existente
- **Integração com Cut:** `start_tc`, `end_tc`, `name` (título), `source` (vídeo original)
- **Cache:** armazenar transcrição e sugestões para evitar reprocessamento
- **Rate limits:** Grok pode ter limites; considerar retry e backoff
