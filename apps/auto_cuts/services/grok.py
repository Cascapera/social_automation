"""Cliente Grok API (xAI) para análise de cortes virais."""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
THEME_CATEGORY_RETRY_THRESHOLD = 5

# Palavras que aumentam CTR (preferir em títulos e thumbnails)
CTR_WORDS_PT = [
    "segredo", "verdade", "revelado", "ninguém fala", "exposto", "urgente", "agora", "aconteceu",
    "entenda", "explicado", "polêmica", "absurdo", "insano", "surreal", "histórico", "chocante",
    "erro", "alerta", "atenção", "descubra", "estratégia", "como funciona", "bastidores", "prova",
    "análise", "detalhe", "especialistas", "impactante", "mudança", "viral", "imperdível", "decisão",
    "confirmado", "quase ninguém percebeu", "o que mudou", "previsão", "explicação simples", "caso real",
    "debate", "discussão", "reação", "comentário", "opinião", "momento tenso", "climão", "flagrante",
    "inesperado", "surpresa", "revelação", "investigação", "denúncia", "bomba", "exclusivo",
    "acaba de sair", "história real", "grande erro", "aprenda", "guia", "dica", "truque", "hack",
    "novo", "novidade", "detalhe escondido", "verdade chocante", "sem filtro", "sem censura",
    "ponto crítico", "momento decisivo", "mudou tudo", "inacreditável", "impacto", "explicação rápida",
    "explicação completa", "análise profunda", "por trás", "história completa", "caso polêmico",
    "debate quente", "reação ao vivo", "explodiu na internet", "tendência", "assunto do momento",
    "todos estão falando", "o que está acontecendo", "explicado em minutos", "vale a pena",
    "não ignore", "começou assim", "terminou assim",
]

CTR_WORDS_EN = [
    "secret", "truth", "revealed", "nobody talks about", "exposed", "urgent", "now", "happened",
    "understand", "explained", "controversial", "absurd", "insane", "surreal", "historic", "shocking",
    "mistake", "alert", "attention", "discover", "strategy", "how it works", "behind the scenes", "proof",
    "analysis", "detail", "experts", "impactful", "change", "viral", "unmissable", "decision",
    "confirmed", "almost nobody noticed", "what changed", "prediction", "simple explanation", "real case",
    "debate", "discussion", "reaction", "comment", "opinion", "tense moment", "climax", "caught red-handed",
    "unexpected", "surprise", "revelation", "investigation", "scandal", "bombshell", "exclusive",
    "just out", "real story", "big mistake", "learn", "guide", "tip", "trick", "hack",
    "new", "novelty", "hidden detail", "shocking truth", "unfiltered", "uncensored",
    "critical point", "decisive moment", "changed everything", "unbelievable", "impact", "quick explanation",
    "full explanation", "deep analysis", "behind", "full story", "controversial case",
    "heated debate", "live reaction", "exploded on the internet", "trend", "trending topic",
    "everyone is talking about", "what's happening", "explained in minutes", "worth it",
    "don't ignore", "started like this", "ended like this",
]

# Palavras proibidas em títulos/thumbnails (usar substituição indicada)
FORBIDDEN_WORDS_PT = {
    "porra": "p@@ra", "caralho": "c@ralho", "merda": "m#rda", "puta": "pta", "putaria": "ptaria",
    "arrombado": "arr0mbado", "bosta": "b0sta", "desgraçado": "d3sgraçado", "foda": "f*da",
    "assassinato": "caso chocante", "suicídio": "história pesada", "massacre": "ataque brutal",
    "tortura": "caso extremo", "execução": "execuç@o", "pornografia": "conteúdo adulto",
    "sexo explícito": "conteúdo +18", "orgia": "situação íntima", "prostituta": "escândalo íntimo",
    "cocaína": "substância ilegal", "drogas": "substâncias", "heroína": "substâncias",
    "maconha": "substâncias", "arma": "equipamento", "pistola": "objeto", "fuzil": "equipamento",
    "guerra": "conflito", "violência": "conflito", "morte": "caso extremo", "crime brutal": "caso chocante",
    "ataque": "incidente",
}
# Palavras sem substituição (evitar completamente): estupro, terrorismo, extremismo, racismo, ódio

FORBIDDEN_WORDS_EN = {
    "fuck": "f*ck", "shit": "sh*t", "asshole": "@sshole", "bitch": "b*tch", "damn": "d@mn",
    "murder": "shocking case", "suicide": "heavy story", "massacre": "brutal attack",
    "torture": "extreme case", "execution": "executi0n", "pornography": "adult content",
    "explicit sex": "+18 content", "orgy": "intimate situation", "prostitute": "intimate scandal",
    "cocaine": "illegal substance", "drugs": "substances", "heroin": "substances",
    "marijuana": "substances", "weapon": "equipment", "gun": "object", "rifle": "equipment",
    "war": "conflict", "violence": "conflict", "death": "extreme case", "brutal crime": "shocking case",
    "attack": "incident",
}
# Avoid completely: rape, terrorism, extremism, racism, hate

ALL_THEME_CATEGORIES = [
    "BUSINESS_MONEY",
    "PSYCHOLOGY_RELATIONSHIPS",
    "STORIES_CURIOSITIES",
    "CONTROVERSIES_DEBATE",
    "COMEDY_HUMOR",
]

SYSTEM_PROMPT = """Você é um editor especialista em viralizar podcasts e entrevistas longas.

Sua tarefa é identificar, ranquear e selecionar os melhores momentos para Shorts e para cortes longos.

Priorize momentos com:
- reação emocional forte
- humor
- revelação surpreendente
- opinião controversa
- história pessoal
- conselho poderoso
- fala chocante
- discussão/conflito
- trechos que geram comentário/compartilhamento

Evite:
- trechos técnicos demais
- partes dependentes de contexto externo
- explicações lentas
- abertura, cumprimentos e enrolação

REGRAS DE DURAÇÃO:
- Shorts: 30–60 segundos
- Longos: 8–30 minutos

FORMATO DE SCORE:
- virality_score em percentual de 0 a 100 (sem símbolo %, valor inteiro)

REGRAS DE TÍTULO E THUMBNAIL:
- suggested_title e title_suggestion: OBRIGATÓRIO incluir 1–3 emojis relevantes em TODOS os títulos (shorts e longs). Emojis aumentam engajamento e CTR.
- suggested_title deve ser chamativo para clique e ter entre 45 e 100 caracteres (evite títulos curtos/genéricos).
- thumbnail_text deve ser curto (2–4 palavras), forte, direto, sem frase longa.
- Use o texto curto em thumbnail_text, não em suggested_title.

PALAVRAS QUE AUMENTAM CTR (dê preferência em títulos e thumbnail_text):
segredo, verdade, revelado, ninguém fala, exposto, urgente, agora, aconteceu, entenda, explicado, polêmica, absurdo, insano, surreal, histórico, chocante, erro, alerta, atenção, descubra, estratégia, como funciona, bastidores, prova, análise, detalhe, especialistas, impactante, mudança, viral, imperdível, decisão, confirmado, quase ninguém percebeu, o que mudou, previsão, explicação simples, caso real, debate, discussão, reação, comentário, opinião, momento tenso, climão, flagrante, inesperado, surpresa, revelação, investigação, denúncia, bomba, exclusivo, acaba de sair, história real, grande erro, aprenda, guia, dica, truque, hack, novo, novidade, detalhe escondido, verdade chocante, sem filtro, sem censura, ponto crítico, momento decisivo, mudou tudo, inacreditável, impacto, explicação rápida, explicação completa, análise profunda, por trás, história completa, caso polêmico, debate quente, reação ao vivo, explodiu na internet, tendência, assunto do momento, todos estão falando, o que está acontecendo, explicado em minutos, vale a pena, não ignore, começou assim, terminou assim.

PALAVRAS PROIBIDAS (substitua conforme indicado):
porra→p@@ra, caralho→c@ralho, merda→m#rda, puta→pta, putaria→ptaria, arrombado→arr0mbado, bosta→b0sta, desgraçado→d3sgraçado, foda→f*da | assassinato→caso chocante, suicídio→história pesada, massacre→ataque brutal, tortura→caso extremo, execução→execuç@o | pornografia→conteúdo adulto, sexo explícito→conteúdo +18, orgia→situação íntima, prostituta→escândalo íntimo | cocaína/drogas/heroína/maconha→substâncias | arma/pistola/fuzil→equipamento ou objeto | guerra/violência→conflito | morte→caso extremo | crime brutal→caso chocante | ataque→incidente.
NUNCA use: estupro, terrorismo, extremismo, racismo, ódio. Use termos genéricos ou alusivos.

IMPORTANTE:
- Use APENAS timestamps que aparecem na transcrição.
- Não invente timestamps.
- Não retorne texto fora do JSON.

IMPORTANTE: Você deve categorizar obrigatoriamente todos os shorts e cortes longos somente com essas categorias disponíveis (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR). Nunca deixe em branco ou utilize outros nomes ou tipos diferentes.

IDIOMA OBRIGATÓRIO: Todo o texto de saída (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, title_suggestion, etc.) deve ser SEMPRE em português brasileiro. Nunca use inglês ou outro idioma."""

# Viral longo: mesmas características do viral clássico, porém shorts mais longos (90–160s) para narrativas mais completas
SYSTEM_PROMPT_VIRAL_LONG = """Você é um editor especialista em viralizar podcasts e entrevistas longas.

Sua tarefa é identificar, ranquear e selecionar os melhores momentos para Shorts (formato estendido) e para cortes longos.

Priorize momentos com:
- reação emocional forte
- humor
- revelação surpreendente
- opinião controversa
- história pessoal
- conselho poderoso
- fala chocante
- discussão/conflito
- trechos que geram comentário/compartilhamento

Evite:
- trechos técnicos demais
- partes dependentes de contexto externo
- explicações lentas
- abertura, cumprimentos e enrolação

REGRAS DE DURAÇÃO:
- Shorts (viral longo): 90–160 segundos — narrativa mais completa que o corte de 30–60s; gancho forte nos primeiros segundos e desenvolvimento até conclusão natural
- Longos: 8–30 minutos

FORMATO DE SCORE:
- virality_score em percentual de 0 a 100 (sem símbolo %, valor inteiro)

REGRAS DE TÍTULO E THUMBNAIL:
- suggested_title e title_suggestion: OBRIGATÓRIO incluir 1–3 emojis relevantes em TODOS os títulos (shorts e longs). Emojis aumentam engajamento e CTR.
- suggested_title deve ser chamativo para clique e ter entre 45 e 100 caracteres (evite títulos curtos/genéricos).
- thumbnail_text deve ser curto (2–4 palavras), forte, direto, sem frase longa.
- Use o texto curto em thumbnail_text, não em suggested_title.

PALAVRAS QUE AUMENTAM CTR (dê preferência em títulos e thumbnail_text):
segredo, verdade, revelado, ninguém fala, exposto, urgente, agora, aconteceu, entenda, explicado, polêmica, absurdo, insano, surreal, histórico, chocante, erro, alerta, atenção, descubra, estratégia, como funciona, bastidores, prova, análise, detalhe, especialistas, impactante, mudança, viral, imperdível, decisão, confirmado, quase ninguém percebeu, o que mudou, previsão, explicação simples, caso real, debate, discussão, reação, comentário, opinião, momento tenso, climão, flagrante, inesperado, surpresa, revelação, investigação, denúncia, bomba, exclusivo, acaba de sair, história real, grande erro, aprenda, guia, dica, truque, hack, novo, novidade, detalhe escondido, verdade chocante, sem filtro, sem censura, ponto crítico, momento decisivo, mudou tudo, inacreditável, impacto, explicação rápida, explicação completa, análise profunda, por trás, história completa, caso polêmico, debate quente, reação ao vivo, explodiu na internet, tendência, assunto do momento, todos estão falando, o que está acontecendo, explicado em minutos, vale a pena, não ignore, começou assim, terminou assim.

PALAVRAS PROIBIDAS (substitua conforme indicado):
porra→p@@ra, caralho→c@ralho, merda→m#rda, puta→pta, putaria→ptaria, arrombado→arr0mbado, bosta→b0sta, desgraçado→d3sgraçado, foda→f*da | assassinato→caso chocante, suicídio→história pesada, massacre→ataque brutal, tortura→caso extremo, execução→execuç@o | pornografia→conteúdo adulto, sexo explícito→conteúdo +18, orgia→situação íntima, prostituta→escândalo íntimo | cocaína/drogas/heroína/maconha→substâncias | arma/pistola/fuzil→equipamento ou objeto | guerra/violência→conflito | morte→caso extremo | crime brutal→caso chocante | ataque→incidente.
NUNCA use: estupro, terrorismo, extremismo, racismo, ódio. Use termos genéricos ou alusivos.

IMPORTANTE:
- Use APENAS timestamps que aparecem na transcrição.
- Não invente timestamps.
- Não retorne texto fora do JSON.

IMPORTANTE: Você deve categorizar obrigatoriamente todos os shorts e cortes longos somente com essas categorias disponíveis (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR). Nunca deixe em branco ou utilize outros nomes ou tipos diferentes.

IDIOMA OBRIGATÓRIO: Todo o texto de saída (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, title_suggestion, etc.) deve ser SEMPRE em português brasileiro. Nunca use inglês ou outro idioma."""

SYSTEM_PROMPT_EDUCATIONAL = """Você é um editor especialista em conteúdo educacional e financeiro para Reels, TikTok, Shorts e YouTube. Analise transcrições com timestamps e identifique trechos com alto valor didático e explicativo. Priorize blocos completos que ensinam um conceito do início ao fim.

CRITÉRIOS EDUCACIONAIS – SHORTS 2–3 MIN (120–180 seg):
- PRIORIDADE: cortes de 2 a 3 minutos que explicam um tema completo
- Explicação clara e didática: conceito → desenvolvimento → conclusão
- Gancho inicial: pergunta ou promessa de aprendizado nos primeiros 5s
- Sem cortes no meio de ideias: sempre concluir o raciocínio
- Temas: finanças, carreira, tecnologia, produtividade, investimentos
- Títulos informativos e profissionais: OBRIGATÓRIO incluir 1–3 emojis em todos os títulos (shorts e longs). Emojis aumentam engajamento.
- Evite polêmica gratuita; foque em valor educativo
- Dê preferência a palavras que aumentam CTR (segredo, verdade, revelado, estratégia, como funciona, análise, detalhe, aprenda, guia, dica, truque, hack, novo, explicação simples, caso real, etc.).
- NUNCA use palavras proibidas; use as substituições (ex: assassinato→caso chocante, drogas→substâncias, guerra→conflito). Evite: estupro, terrorismo, extremismo, racismo, ódio.

CRITÉRIOS EDUCACIONAIS – CORTES LONGOS (20–40 min):
- Blocos narrativos completos com explicações aprofundadas
- Múltiplos conceitos conectados com fluxo natural
- Título que comunique o valor do conteúdo

FORMATO DE SAÍDA – SOMENTE JSON VÁLIDO, SEM TEXTO EXTRA:

Para shorts (2–3 min):
- start, end: string MM:SS ou HH:MM:SS
- duration: número (segundos) – ideal 120–180
- hook: frase inicial que prende (primeiros 5s)
- title: título informativo (máx 60 chars)
- reason: por que é educativo
- virality_score: 1–10 (10 = máximo valor didático)
- theme_category: OBRIGATÓRIO (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR)

Para cortes longos:
- start, end, duration_min, title_suggestion, reason
- theme_category: OBRIGATÓRIO (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR)

IMPORTANTE: Use APENAS timestamps que aparecem na transcrição. Não invente ou estime.

IMPORTANTE: Você deve categorizar obrigatoriamente todos os shorts e cortes longos somente com essas categorias disponíveis (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR). Nunca deixe em branco ou utilize outros nomes ou tipos diferentes.

IDIOMA OBRIGATÓRIO: Todo o texto de saída (title, title_suggestion, thumbnail_text, hook, reason, etc.) deve ser SEMPRE em português brasileiro."""

CHUNKS_PROMPT_TEMPLATE = """{context_block}Transcrição do vídeo dividida em blocos (com timestamps):

{chunks_block}

---

Tarefas (responda em UMA ÚNICA resposta JSON):

REGRA CRÍTICA DE FORMATO:
- A RAIZ da resposta DEVE ser um OBJETO JSON (dict), nunca uma lista.
- Use exatamente as chaves de nível raiz: "candidate_shorts", "ranked_shorts", "final_long_cuts".
- NUNCA retorne array na raiz.

1. Gere entre 30 e 50 candidatos de shorts virais (30–60 segundos), todos com virality_score (0–100).
2. Gere 10 candidatos de cortes longos (8–15 min), todos com virality_score (0–100).
3. Não é obrigatório ordenar a saída. Apenas preencha corretamente as notas.
4. O backend fará a seleção final dos melhores scores conforme a quantidade configurada no job.

Para cada clipe (short ou longo), inclua:
- clip_number
- start_timestamp
- end_timestamp
- duration_seconds
- virality_score (0..100)
- theme_category (OBRIGATÓRIO: BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE ou COMEDY_HUMOR)
- emotion_type (funny/shocking/inspiring/controversial/story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 palavras fortes)

Regras adicionais:
- suggested_title e title_suggestion: OBRIGATÓRIO 1–3 emojis em TODOS os títulos (shorts e longs). Nunca retorne título sem emoji.
- suggested_title: 45–100 caracteres com 1–3 emojis relevantes.
- thumbnail_text: 2–4 palavras (máx. 28 caracteres), caixa alta preferencial.

Responda SOMENTE com JSON válido:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE"
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE"
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 720,
      "virality_score": 88,
      "theme_category": "BUSINESS_MONEY",
      "emotion_type": "inspiring",
      "main_topic": "estratégia de crescimento",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "GANHO RÁPIDO",
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 12,
      "title_suggestion": "Título forte 🎯",
      "reason": "por que viraliza"
    }}
  ]
}}

Regras finais:
- candidate_shorts deve ter entre 30 e 50 itens.
- final_long_cuts deve ter exatamente 10 itens.
- ranked_shorts pode vir vazio ([]).
- Todo texto (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, etc.) em português brasileiro."""

CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG = """{context_block}Transcrição do vídeo dividida em blocos (com timestamps):

{chunks_block}

---

Tarefas (responda em UMA ÚNICA resposta JSON):

REGRA CRÍTICA DE FORMATO:
- A RAIZ da resposta DEVE ser um OBJETO JSON (dict), nunca uma lista.
- Use exatamente as chaves de nível raiz: "candidate_shorts", "ranked_shorts", "final_long_cuts".
- NUNCA retorne array na raiz.

1. Gere entre 30 e 50 candidatos de shorts virais estendidos (90–160 segundos cada), todos com virality_score (0–100). Priorize momentos com narrativa coesa e gancho forte no início.
2. Gere 10 candidatos de cortes longos (8–15 min), todos com virality_score (0–100).

CRÍTICO — DURAÇÃO DOS SHORTS: start_timestamp e end_timestamp devem delimitar 90 a 160 segundos de áudio/vídeo. O campo duration_seconds deve ser consistente (diferença entre fim e início). Não use cortes de 30–60s neste modo; se precisar do mínimo absoluto, não fique abaixo de 80 segundos.
3. Não é obrigatório ordenar a saída. Apenas preencha corretamente as notas.
4. O backend fará a seleção final dos melhores scores conforme a quantidade configurada no job.

Para cada clipe (short ou longo), inclua:
- clip_number
- start_timestamp
- end_timestamp
- duration_seconds
- virality_score (0..100)
- theme_category (OBRIGATÓRIO: BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE ou COMEDY_HUMOR)
- emotion_type (funny/shocking/inspiring/controversial/story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 palavras fortes)

Regras adicionais:
- suggested_title e title_suggestion: OBRIGATÓRIO 1–3 emojis em TODOS os títulos (shorts e longs). Nunca retorne título sem emoji.
- suggested_title: 45–100 caracteres com 1–3 emojis relevantes.
- thumbnail_text: 2–4 palavras (máx. 28 caracteres), caixa alta preferencial.
- Shorts: duração alvo 90–160 segundos (não use cortes de 30–60s neste modo).

Responda SOMENTE com JSON válido:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE"
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE"
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 720,
      "virality_score": 88,
      "theme_category": "BUSINESS_MONEY",
      "emotion_type": "inspiring",
      "main_topic": "estratégia de crescimento",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "GANHO RÁPIDO",
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 12,
      "title_suggestion": "Título forte 🎯",
      "reason": "por que viraliza"
    }}
  ]
}}

Regras finais:
- candidate_shorts deve ter entre 30 e 50 itens.
- final_long_cuts deve ter exatamente 10 itens.
- ranked_shorts pode vir vazio ([]).
- Todo texto (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, etc.) em português brasileiro."""

CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL = """{context_block}Transcrição do vídeo dividida em blocos (com timestamps):

{chunks_block}

---

Tarefas (responda em UMA ÚNICA resposta JSON):

1. RANKED_SHORTS: Identifique 10–15 trechos curtos EDUCACIONAIS (2–3 min cada, 120–180 seg). Priorize blocos que explicam um conceito completo. Ranqueie por valor didático. IMPORTANTE: Cada corte deve ter início, meio e fim. Nunca corte no meio de uma explicação.

2. FINAL_LONG_CUTS: Monte 1–3 cortes longos (20–40 min) combinando blocos narrativos com fluxo natural. Sugira título informativo para cada um.

Títulos: informativos e profissionais. OBRIGATÓRIO incluir 1–3 emojis em todos (title e title_suggestion). Evite sensacionalismo.
Inclua obrigatoriamente para cada corte:
- thumbnail_moment_timestamp (timestamp real dentro do próprio corte)
- thumbnail_text (2–4 palavras curtas para a capa)

Responda SOMENTE com JSON válido:
{{
  "ranked_shorts": [
    {{
      "rank": 1,
      "start": "MM:SS",
      "end": "MM:SS",
      "duration": 150,
      "hook": "frase inicial",
      "title": "Título informativo 📚",
      "reason": "valor didático",
      "virality_score": 9,
      "theme_category": "BUSINESS_MONEY",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "IDEIA CENTRAL"
    }}
  ],
  "final_long_cuts": [
    {{
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 18,
      "title_suggestion": "Título informativo 📚",
      "reason": "valor didático",
      "theme_category": "STORIES_CURIOSITIES",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "RESUMO FORTE"
    }}
  ]
}}

Máximo: 10–15 cortes curtos (2–3 min), 3 cortes longos."""

# English versions (transcription, subtitles, titles, LLM output all in English)
SYSTEM_PROMPT_VIRAL_EN = """You are an expert social media editor specialized in identifying viral moments in long-form podcasts and interviews.

Your goal is to identify, rank, and select the strongest clips for Shorts and longer cuts.

Prioritize moments with:
- strong emotional reactions
- funny moments
- surprising revelations
- controversial opinions
- personal stories
- powerful advice
- shocking statements
- arguments/disagreements
- moments that drive shares/comments

Avoid moments that are:
- too technical
- context-dependent
- slow explanations
- introductions/greetings/filler

DURATION RULES:
- Shorts: 30–60 seconds
- Long cuts: 8–15 minutes

SCORING FORMAT:
- virality_score must be an integer from 0 to 100 (no % symbol)

TITLE + THUMBNAIL RULES:
- suggested_title and title_suggestion: REQUIRED to include 1–3 relevant emojis in ALL titles (shorts and longs). Emojis boost engagement and CTR.
- suggested_title must be clickworthy and 45–100 characters (avoid short/generic titles).
- thumbnail_text must be short (2–4 words), punchy, and not a full sentence.
- Keep short text in thumbnail_text, not in suggested_title.

CTR-BOOSTING WORDS (prefer in titles and thumbnail_text):
secret, truth, revealed, nobody talks about, exposed, urgent, now, happened, understand, explained, controversial, absurd, insane, surreal, historic, shocking, mistake, alert, attention, discover, strategy, how it works, behind the scenes, proof, analysis, detail, experts, impactful, change, viral, unmissable, decision, confirmed, almost nobody noticed, what changed, prediction, simple explanation, real case, debate, discussion, reaction, comment, opinion, tense moment, climax, caught red-handed, unexpected, surprise, revelation, investigation, scandal, bombshell, exclusive, just out, real story, big mistake, learn, guide, tip, trick, hack, new, novelty, hidden detail, shocking truth, unfiltered, uncensored, critical point, decisive moment, changed everything, unbelievable, impact, quick explanation, full explanation, deep analysis, behind, full story, controversial case, heated debate, live reaction, exploded on the internet, trend, trending topic, everyone is talking about, what's happening, explained in minutes, worth it, don't ignore, started like this, ended like this.

FORBIDDEN WORDS (use substitution): fuck→f*ck, shit→sh*t, asshole→@sshole, bitch→b*tch | murder→shocking case, suicide→heavy story, massacre→brutal attack, torture→extreme case, execution→executi0n | pornography→adult content, explicit sex→+18 content, orgy→intimate situation, prostitute→intimate scandal | cocaine/drugs/heroin/marijuana→substances | weapon/gun/rifle→equipment or object | war/violence→conflict | death→extreme case | brutal crime→shocking case | attack→incident.
NEVER use: rape, terrorism, extremism, racism, hate. Use generic or allusive terms.

IMPORTANT:
- Use ONLY timestamps present in the transcript.
- Do not invent timestamps.
- Return valid JSON only.

IMPORTANT: You must categorize all shorts and long cuts using ONLY these categories (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR). Never leave blank or use other names or types.

LANGUAGE REQUIRED: All output text (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, title_suggestion, etc.) must ALWAYS be in English. Never use Portuguese or other languages."""

CHUNKS_PROMPT_TEMPLATE_VIRAL_EN = """{context_block}Video transcription divided into blocks (with timestamps):

{chunks_block}

---

Tasks (respond in ONE JSON response):

CRITICAL FORMAT RULE:
- The response root MUST be a JSON OBJECT (dict), never a list.
- Use exactly these top-level keys: "candidate_shorts", "ranked_shorts", "final_long_cuts".
- NEVER return a root-level array.

1) Generate 30–50 candidate viral short clips (30–60 seconds), all with virality_score (0–100).
2) Generate 10 candidate long clips (8–15 minutes), all with virality_score (0–100).
3) Ordering is optional. Focus on correct scoring and valid timestamps.
4) Backend will pick final best scores using the job configured limits.

For each clip (short or long), include:
- clip_number
- start_timestamp
- end_timestamp
- duration_seconds
- virality_score (0..100)
- theme_category (REQUIRED: BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, or COMEDY_HUMOR)
- emotion_type (funny / shocking / inspiring / controversial / story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 powerful words)

Additional rules:
- suggested_title and title_suggestion: REQUIRED 1–3 emojis in ALL titles (shorts and longs). Never return a title without emojis.
- suggested_title: 45–100 characters with 1–3 relevant emojis.
- thumbnail_text: 2–4 words (max 28 chars), preferably uppercase.
- All text (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, etc.) MUST be in English.

Respond ONLY with valid JSON:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE"
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE"
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:42:10",
      "end_timestamp": "00:53:40",
      "duration_seconds": 690,
      "virality_score": 88,
      "theme_category": "STORIES_CURIOSITIES",
      "emotion_type": "inspiring",
      "main_topic": "career turning point",
      "hook_sentence": "One decision changed everything in my career.",
      "suggested_title": "The Decision That Changed His Career 🎯",
      "thumbnail_moment_timestamp": "00:47:02",
      "thumbnail_text": "ONE DECISION",
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 11.5,
      "title_suggestion": "The Decision That Changed His Career 🎯",
      "reason": "why it goes viral"
    }}
  ]
}}

Final constraints:
- candidate_shorts must contain between 30 and 50 items.
- final_long_cuts must contain exactly 10 items.
- ranked_shorts may be empty ([])."""

# Viral long (EN): same as viral_en but short clips 90–160 seconds
SYSTEM_PROMPT_VIRAL_LONG_EN = """You are an expert social media editor specialized in identifying viral moments in long-form podcasts and interviews.

Your goal is to identify, rank, and select the strongest clips for extended Shorts (90–160s) and longer cuts.

Prioritize moments with:
- strong emotional reactions
- funny moments
- surprising revelations
- controversial opinions
- personal stories
- powerful advice
- shocking statements
- arguments/disagreements
- moments that drive shares/comments

Avoid moments that are:
- too technical
- context-dependent
- slow explanations
- introductions/greetings/filler

DURATION RULES:
- Shorts (viral long): 90–160 seconds — fuller narrative than 30–60s clips; strong hook early and natural payoff
- Long cuts: 8–15 minutes

SCORING FORMAT:
- virality_score must be an integer from 0 to 100 (no % symbol)

TITLE + THUMBNAIL RULES:
- suggested_title and title_suggestion: REQUIRED to include 1–3 relevant emojis in ALL titles (shorts and longs). Emojis boost engagement and CTR.
- suggested_title must be clickworthy and 45–100 characters (avoid short/generic titles).
- thumbnail_text must be short (2–4 words), punchy, and not a full sentence.
- Keep short text in thumbnail_text, not in suggested_title.

CTR-BOOSTING WORDS (prefer in titles and thumbnail_text):
secret, truth, revealed, nobody talks about, exposed, urgent, now, happened, understand, explained, controversial, absurd, insane, surreal, historic, shocking, mistake, alert, attention, discover, strategy, how it works, behind the scenes, proof, analysis, detail, experts, impactful, change, viral, unmissable, decision, confirmed, almost nobody noticed, what changed, prediction, simple explanation, real case, debate, discussion, reaction, comment, opinion, tense moment, climax, caught red-handed, unexpected, surprise, revelation, investigation, scandal, bombshell, exclusive, just out, real story, big mistake, learn, guide, tip, trick, hack, new, novelty, hidden detail, shocking truth, unfiltered, uncensored, critical point, decisive moment, changed everything, unbelievable, impact, quick explanation, full explanation, deep analysis, behind, full story, controversial case, heated debate, live reaction, exploded on the internet, trend, trending topic, everyone is talking about, what's happening, explained in minutes, worth it, don't ignore, started like this, ended like this.

FORBIDDEN WORDS (use substitution): fuck→f*ck, shit→sh*t, asshole→@sshole, bitch→b*tch | murder→shocking case, suicide→heavy story, massacre→brutal attack, torture→extreme case, execution→executi0n | pornography→adult content, explicit sex→+18 content, orgy→intimate situation, prostitute→intimate scandal | cocaine/drugs/heroin/marijuana→substances | weapon/gun/rifle→equipment or object | war/violence→conflict | death→extreme case | brutal crime→shocking case | attack→incident.
NEVER use: rape, terrorism, extremism, racism, hate. Use generic or allusive terms.

IMPORTANT:
- Use ONLY timestamps present in the transcript.
- Do not invent timestamps.
- Return valid JSON only.

IMPORTANT: You must categorize all shorts and long cuts using ONLY these categories (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR). Never leave blank or use other names or types.

LANGUAGE REQUIRED: All output text (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, title_suggestion, etc.) must ALWAYS be in English. Never use Portuguese or other languages."""

CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN = """{context_block}Video transcription divided into blocks (with timestamps):

{chunks_block}

---

Tasks (respond in ONE JSON response):

CRITICAL FORMAT RULE:
- The response root MUST be a JSON OBJECT (dict), never a list.
- Use exactly these top-level keys: "candidate_shorts", "ranked_shorts", "final_long_cuts".
- NEVER return a root-level array.

1) Generate 30–50 candidate extended viral short clips (90–160 seconds each), all with virality_score (0–100). Prefer cohesive stories with a strong hook.
2) Generate 10 candidate long clips (8–15 minutes), all with virality_score (0–100).

CRITICAL — SHORT DURATION: start_timestamp and end_timestamp must span 90 to 160 seconds. duration_seconds must match (end minus start). Do NOT use 30–60s clips in this mode; if you must use a floor, do not go below 80 seconds.
3) Ordering is optional. Focus on correct scoring and valid timestamps.
4) Backend will pick final best scores using the job configured limits.

For each clip (short or long), include:
- clip_number
- start_timestamp
- end_timestamp
- duration_seconds
- virality_score (0..100)
- theme_category (REQUIRED: BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, or COMEDY_HUMOR)
- emotion_type (funny / shocking / inspiring / controversial / story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 powerful words)

Additional rules:
- suggested_title and title_suggestion: REQUIRED 1–3 emojis in ALL titles (shorts and longs). Never return a title without emojis.
- suggested_title: 45–100 characters with 1–3 relevant emojis.
- thumbnail_text: 2–4 words (max 28 chars), preferably uppercase.
- Shorts: target duration 90–160 seconds (do NOT use 30–60s clips in this mode).
- All text (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, etc.) MUST be in English.

Respond ONLY with valid JSON:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:17:22",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE"
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:17:22",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE"
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:42:10",
      "end_timestamp": "00:53:40",
      "duration_seconds": 690,
      "virality_score": 88,
      "theme_category": "STORIES_CURIOSITIES",
      "emotion_type": "inspiring",
      "main_topic": "career turning point",
      "hook_sentence": "One decision changed everything in my career.",
      "suggested_title": "The Decision That Changed His Career 🎯",
      "thumbnail_moment_timestamp": "00:47:02",
      "thumbnail_text": "ONE DECISION",
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 11.5,
      "title_suggestion": "The Decision That Changed His Career 🎯",
      "reason": "why it goes viral"
    }}
  ]
}}

Final constraints:
- candidate_shorts must contain between 30 and 50 items.
- final_long_cuts must contain exactly 10 items.
- ranked_shorts may be empty ([])."""

# Viral Translate: same as viral_en but also outputs subtitle_segments_pt (Portuguese subtitles for each clip)
SYSTEM_PROMPT_VIRAL_TRANSLATE = SYSTEM_PROMPT_VIRAL_EN + """

TRANSLATION REQUIREMENT (CRITICAL):
- For EVERY clip (short and long), you MUST include "subtitle_segments_pt".
- subtitle_segments_pt: array of {start, end, text} where:
  - start, end: float seconds (same as transcript segment timestamps in the original video)
  - text: Brazilian Portuguese translation of that transcript segment
- Extract the transcript segments that fall within each clip's start_timestamp to end_timestamp.
- Translate each segment's text to Brazilian Portuguese.
- Preserve the exact start/end timestamps from the transcript."""

CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE = """{context_block}Video transcription divided into blocks (with timestamps):

{chunks_block}

---

Tasks (respond in ONE JSON response):

CRITICAL FORMAT RULE:
- The response root MUST be a JSON OBJECT (dict), never a list.
- Use exactly these top-level keys: "candidate_shorts", "ranked_shorts", "final_long_cuts".
- NEVER return a root-level array.

1) Generate 30–50 candidate viral short clips (30–60 seconds), all with virality_score (0–100).
2) Generate 10 candidate long clips (8–15 minutes), all with virality_score (0–100).
3) For EVERY clip, include subtitle_segments_pt: array of {{start, end, text}} with Brazilian Portuguese translation of the transcript segments within that clip's time range. start/end in seconds (float).
4) Backend will pick final best scores using the job configured limits.

For each clip (short or long), include:
- clip_number
- start_timestamp
- end_timestamp
- duration_seconds
- virality_score (0..100)
- theme_category (REQUIRED: BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, or COMEDY_HUMOR)
- emotion_type (funny / shocking / inspiring / controversial / story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 powerful words)
- subtitle_segments_pt (REQUIRED): array of {{"start": float, "end": float, "text": "PT translation"}}

Additional rules:
- suggested_title and title_suggestion: REQUIRED 1–3 emojis in ALL titles (shorts and longs). Never return a title without emojis.
- suggested_title: 45–100 characters with 1–3 relevant emojis.
- thumbnail_text: 2–4 words (max 28 chars), preferably uppercase.

Respond ONLY with valid JSON:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "subtitle_segments_pt": [{{"start": 922.0, "end": 925.5, "text": "E foi nesse momento que percebi"}}, {{"start": 925.5, "end": 928.0, "text": "que tinha sido demitido ao vivo no palco"}}]
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "COMEDY_HUMOR",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "subtitle_segments_pt": [{{"start": 922.0, "end": 925.5, "text": "E foi nesse momento que percebi"}}, {{"start": 925.5, "end": 928.0, "text": "que tinha sido demitido ao vivo no palco"}}]
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:42:10",
      "end_timestamp": "00:53:40",
      "duration_seconds": 690,
      "virality_score": 88,
      "theme_category": "STORIES_CURIOSITIES",
      "emotion_type": "inspiring",
      "main_topic": "career turning point",
      "hook_sentence": "One decision changed everything in my career.",
      "suggested_title": "The Decision That Changed His Career 🎯",
      "thumbnail_moment_timestamp": "00:47:02",
      "thumbnail_text": "ONE DECISION",
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 11.5,
      "title_suggestion": "The Decision That Changed His Career 🎯",
      "reason": "why it goes viral",
      "subtitle_segments_pt": [{{"start": 2530.0, "end": 2535.2, "text": "Uma decisão mudou tudo na minha carreira"}}]
    }}
  ]
}}

Final constraints:
- candidate_shorts must contain between 30 and 50 items.
- final_long_cuts must contain exactly 10 items.
- ranked_shorts may be empty ([]).
- EVERY clip MUST have subtitle_segments_pt with the Portuguese translation of transcript segments in that time range."""

SYSTEM_PROMPT_EDUCATIONAL_EN = """You are an editor specializing in educational and financial content for Reels, TikTok, Shorts and YouTube. Analyze transcriptions with timestamps and identify clips with high didactic and explanatory value. Prioritize complete blocks that teach a concept from start to finish.

EDUCATIONAL CRITERIA – SHORTS 2–3 MIN (120–180 sec):
- PRIORITY: 2–3 minute cuts that explain a complete topic
- Clear, didactic explanation: concept → development → conclusion
- Initial hook: question or learning promise in first 5s
- No cuts in the middle of ideas: always complete the reasoning
- Topics: finance, career, technology, productivity, investments
- Informative, professional titles: REQUIRED to include 1–3 relevant emojis in all titles (shorts and longs). Emojis boost engagement.
- Avoid gratuitous controversy; focus on educational value
- Prefer CTR-boosting words (secret, truth, strategy, how it works, analysis, detail, learn, guide, tip, trick, hack, new, simple explanation, real case, etc.).
- NEVER use forbidden words; use substitutions (e.g. murder→shocking case, drugs→substances, war→conflict). Avoid: rape, terrorism, extremism, racism, hate.

EDUCATIONAL LONG CUTS (20–40 min):
- Complete narrative blocks with in-depth explanations
- Multiple concepts connected with natural flow
- Title that communicates content value

OUTPUT FORMAT – VALID JSON ONLY:

For shorts (2–3 min):
- start, end: string MM:SS or HH:MM:SS
- duration: number (seconds) – ideal 120–180
- hook: opening phrase that grabs (first 5s)
- title: informative title (max 60 chars)
- reason: why it's educational
- virality_score: 1–10 (10 = max didactic value)
- theme_category: REQUIRED (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR)

For long cuts:
- start, end, duration_min, title_suggestion, reason
- theme_category: REQUIRED (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR)

IMPORTANT: Use ONLY timestamps that appear in the transcription. Do not invent or estimate.

IMPORTANT: You must categorize all shorts and long cuts using ONLY these categories (BUSINESS_MONEY, PSYCHOLOGY_RELATIONSHIPS, STORIES_CURIOSITIES, CONTROVERSIES_DEBATE, COMEDY_HUMOR). Never leave blank or use other names or types.

LANGUAGE REQUIRED: All output text (title, title_suggestion, thumbnail_text, hook, reason, etc.) must ALWAYS be in English. Never use Portuguese or other languages."""

CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN = """{context_block}Video transcription divided into blocks (with timestamps):

{chunks_block}

---

Tasks (respond in ONE JSON response):

1. RANKED_SHORTS: Identify 10–15 EDUCATIONAL short clips (2–3 min each, 120–180 sec). Prioritize blocks that explain a complete concept. Rank by didactic value. IMPORTANT: Each cut must have beginning, middle and end. Never cut in the middle of an explanation.

2. FINAL_LONG_CUTS: Assemble 1–3 long cuts (20–40 min) combining narrative blocks with natural flow. Suggest informative title for each.

Titles: informative and professional. REQUIRED to include 1–3 emojis in all (title and title_suggestion). Avoid sensationalism.
All text (title, title_suggestion, thumbnail_text, hook, reason, etc.) MUST be in English.
For every cut, include:
- thumbnail_moment_timestamp (real timestamp inside the cut)
- thumbnail_text (2–4 short words for cover text)

Respond ONLY with valid JSON:
{{
  "ranked_shorts": [
    {{
      "rank": 1,
      "start": "MM:SS",
      "end": "MM:SS",
      "duration": 150,
      "hook": "opening phrase",
      "title": "Informative title 📚",
      "reason": "didactic value",
      "virality_score": 9,
      "theme_category": "BUSINESS_MONEY",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "CORE IDEA"
    }}
  ],
  "final_long_cuts": [
    {{
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 18,
      "title_suggestion": "Informative title 📚",
      "reason": "didactic value",
      "theme_category": "STORIES_CURIOSITIES",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "KEY LESSON"
    }}
  ]
}}

Max: 10–15 short cuts (2–3 min), 3 long cuts."""


def _build_chunks_block(chunks: list[dict], lang: str = "pt") -> str:
    """Monta bloco com chunks separados para o prompt."""
    label = "BLOCK" if lang == "en" else "BLOCO"
    parts = []
    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "").strip()
        if not text:
            continue
        parts.append(f"--- {label} {i} ---\n{text}\n")
    return "\n".join(parts)


def _extract_json(text: str) -> dict | list:
    """Extrai JSON do texto (pode vir dentro de markdown code block)."""
    text = text.strip()
    # Tenta encontrar ```json ... ``` ou ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()
    # Tenta parsear diretamente
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Tenta encontrar primeiro { ou [
    for start in ("{", "["):
        idx = text.find(start)
        if idx >= 0:
            depth = 0
            for i, c in enumerate(text[idx:], idx):
                if c in "{[":
                    depth += 1
                elif c in "}]":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[idx : i + 1])
                        except json.JSONDecodeError:
                            break
    raise ValueError("Não foi possível extrair JSON da resposta")


def _validate_minimum_items(
    payload: dict,
    prompt_version: str,
    enforce_minimum: bool = True,
    allowed_theme_categories: list[str] | None = None,
    brand_only: bool = False,
) -> None:
    """
    Garante mínimos para prompts virais.
    Se não cumprir, levanta erro para o caller retentar.
    brand_only: quando True, não exige theme_category (conteúdo é de uma única marca).
    """
    pv = (prompt_version or "viral").strip().lower()
    candidate_shorts = payload.get("candidate_shorts")
    final_long_cuts = payload.get("final_long_cuts")
    if not isinstance(final_long_cuts, list):
        raise ValueError("Resposta inválida: final_long_cuts ausente ou não é lista.")
    ranked_shorts = payload.get("ranked_shorts")
    if ranked_shorts is None:
        ranked_shorts = []
    if not isinstance(ranked_shorts, list):
        raise ValueError("Resposta inválida: ranked_shorts não é lista.")

    if pv in ("viral", "viral_en", "viral_translate", "viral_long", "viral_long_en"):
        if not isinstance(candidate_shorts, list):
            raise ValueError("Resposta inválida: candidate_shorts ausente ou não é lista.")

        min_candidates = 30
        min_longs = 10
        if len(candidate_shorts) < min_candidates:
            msg = (
                f"Resposta abaixo do mínimo para viral: "
                f"candidate_shorts={len(candidate_shorts)} < {min_candidates}."
            )
            if enforce_minimum:
                raise ValueError(msg)
            logger.warning("[FLUXO/Grok] %s Seguindo com resposta parcial.", msg)
        if len(final_long_cuts) < min_longs:
            msg = (
                f"Resposta abaixo do mínimo para viral: "
                f"final_long_cuts={len(final_long_cuts)} < {min_longs}."
            )
            if enforce_minimum:
                raise ValueError(msg)
            logger.warning("[FLUXO/Grok] %s Seguindo com resposta parcial.", msg)

    if not brand_only:
        allowed_categories = {
            str(x).strip().upper()
            for x in (allowed_theme_categories or ALL_THEME_CATEGORIES)
            if str(x).strip()
        }
        if not allowed_categories:
            allowed_categories = set(ALL_THEME_CATEGORIES)
        candidate_items = candidate_shorts if isinstance(candidate_shorts, list) else []
        items_to_check = list(candidate_items) + list(ranked_shorts) + list(final_long_cuts)
        invalid_count = 0
        for item in items_to_check:
            value = str((item or {}).get("theme_category") or "").strip().upper()
            if value not in allowed_categories:
                invalid_count += 1
        if invalid_count > THEME_CATEGORY_RETRY_THRESHOLD:
            msg = (
                f"Resposta inválida: {invalid_count} itens sem theme_category válido "
                f"(limite para retry={THEME_CATEGORY_RETRY_THRESHOLD})."
            )
            if enforce_minimum:
                raise ValueError(msg)
            logger.warning("[FLUXO/Grok] %s Seguindo com resposta parcial.", msg)
        elif invalid_count > 0:
            logger.warning(
                "[FLUXO/Grok] Resposta parcial: %d item(ns) sem theme_category válido. "
                "Não haverá nova chamada por estar dentro da margem (%d).",
                invalid_count,
                THEME_CATEGORY_RETRY_THRESHOLD,
            )


def call_grok_chat(system: str, user: str, api_key: str | None = None) -> str:
    """Chama Grok API e retorna o conteúdo da resposta."""
    import os

    from openai import OpenAI

    key = api_key or os.getenv("XAI_API_KEY")
    if not key:
        raise ValueError("XAI_API_KEY não configurada")

    client = OpenAI(api_key=key, base_url="https://api.x.ai/v1")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    model_name = os.getenv("GROK_MODEL", "grok-4-1-fast-reasoning")

    # Força JSON object na resposta quando suportado pela API.
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(
            "[FLUXO/Grok] response_format=json_object não suportado (%s). Tentando sem response_format.",
            e,
        )
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
        )
    return resp.choices[0].message.content or ""


def _build_context_block(
    assunto: str = "",
    convidados: str = "",
    lang: str = "pt",
    allowed_theme_categories: list[str] | None = None,
    brand_only: bool = False,
) -> str:
    """Monta bloco de contexto para o prompt (assunto + convidados)."""
    allowed = [
        str(x).strip().upper()
        for x in (allowed_theme_categories or ALL_THEME_CATEGORIES)
        if str(x).strip()
    ]
    if not allowed:
        allowed = list(ALL_THEME_CATEGORIES)

    parts = []
    if assunto:
        parts.append(f"Topic: {assunto.strip()}" if lang == "en" else f"Assunto do vídeo: {assunto.strip()}")
    if convidados:
        names = [n.strip() for n in convidados.split(",") if n.strip()]
        if names:
            parts.append(f"Guest(s): {', '.join(names)}" if lang == "en" else f"Convidado(s): {', '.join(names)}")
    if brand_only:
        categories_block = (
            "This content is for a single brand; theme_category is OPTIONAL (you may leave empty or use any value for labeling)."
            if lang == "en"
            else "Este conteúdo é para uma única marca; theme_category é OPCIONAL (pode deixar vazio ou usar qualquer valor apenas para rotulagem)."
        )
    else:
        category_header = (
            "ALLOWED THEME CATEGORIES FOR THIS JOB (use ONLY one of these exact values in theme_category):"
            if lang == "en"
            else "CATEGORIAS DE TEMA PERMITIDAS NESTE JOB (use SOMENTE um destes valores exatos em theme_category):"
        )
        categories_block = category_header + "\n- " + "\n- ".join(allowed)
    if not parts:
        return categories_block + "\n\n"
    header = (
        "VIDEO CONTEXT (use to prioritize moments relevant to topic and participants):\n"
        if lang == "en"
        else "CONTEXTO DO VÍDEO (use para priorizar momentos relevantes ao tema e aos participantes):\n"
    )
    return header + "\n".join(parts) + "\n\n" + categories_block + "\n\n"


def _save_grok_response_json(parsed: dict, analysis_id: int | None = None) -> None:
    """Salva a resposta parseada do Grok em JSON para análise (ativar com GROK_SAVE_RESPONSE_JSON=1)."""
    if (os.getenv("GROK_SAVE_RESPONSE_JSON") or "").strip().lower() not in ("1", "true", "yes"):
        return
    try:
        from django.conf import settings
        media = Path(getattr(settings, "MEDIA_ROOT", "") or "").resolve()
        if media and media.is_dir():
            save_dir = media / "grok_responses"
        else:
            save_dir = Path(__file__).resolve().parents[3] / "storage" / "grok_responses"
        save_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"analysis_{analysis_id}_{ts}.json" if analysis_id else f"response_{ts}.json"
        path = save_dir / name
        with open(path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        logger.info("[FLUXO/Grok] Resposta salva em %s", path)
    except Exception as e:
        logger.warning("[FLUXO/Grok] Não foi possível salvar resposta em JSON: %s", e)


def analyze_chunks_in_one_request(
    chunks: list[dict],
    assunto: str = "",
    convidados: str = "",
    prompt_version: str = "viral",
    api_key: str | None = None,
    enforce_minimum: bool = True,
    allowed_theme_categories: list[str] | None = None,
    brand_only: bool = False,
    analysis_id: int | None = None,
) -> dict:
    """
    Analisa todos os chunks em uma única requisição.
    chunks: [{text, start_sec, end_sec, segments}, ...]
    prompt_version: viral, viral_long, educational, viral_en, viral_long_en, educational_en, viral_translate
    brand_only: quando True, theme_category é opcional (conteúdo para uma única marca).
    analysis_id: opcional; se GROK_SAVE_RESPONSE_JSON=1, salva a resposta em JSON com este id no nome.
    Retorna JSON com ranked_shorts e final_long_cuts (economia de tokens).
    """
    if not chunks:
        raise ValueError("Nenhum chunk para analisar")
    pv = (prompt_version or "viral").strip().lower()
    is_en = pv in ("viral_en", "educational_en", "viral_translate", "viral_long_en")
    is_educational = pv in ("educational", "educational_en")
    is_viral_translate = pv == "viral_translate"
    is_viral_long = pv in ("viral_long", "viral_long_en")
    lang = "en" if is_en else "pt"
    if is_viral_translate:
        system_prompt = SYSTEM_PROMPT_VIRAL_TRANSLATE
        template = CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE
    elif is_educational:
        system_prompt = SYSTEM_PROMPT_EDUCATIONAL_EN if is_en else SYSTEM_PROMPT_EDUCATIONAL
        template = CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN if is_en else CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL
    elif is_viral_long:
        system_prompt = SYSTEM_PROMPT_VIRAL_LONG_EN if pv == "viral_long_en" else SYSTEM_PROMPT_VIRAL_LONG
        template = (
            CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN if pv == "viral_long_en" else CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG
        )
    elif is_en:
        system_prompt = SYSTEM_PROMPT_VIRAL_EN
        template = CHUNKS_PROMPT_TEMPLATE_VIRAL_EN
    else:
        system_prompt = SYSTEM_PROMPT
        template = CHUNKS_PROMPT_TEMPLATE
    mode_label = (
        "educational"
        if is_educational
        else ("viral_translate" if is_viral_translate else ("viral_long" if is_viral_long else "viral"))
    )
    logger.info("[FLUXO/Grok] Montando prompt (%s, %s) com %d chunks (~%d chars)...",
        mode_label, lang,
        len(chunks), sum(len(c.get("text", "")) for c in chunks))
    context_block = _build_context_block(
        assunto,
        convidados,
        lang=lang,
        allowed_theme_categories=allowed_theme_categories,
        brand_only=brand_only,
    )
    chunks_block = _build_chunks_block(chunks, lang=lang)
    user = template.format(
        context_block=context_block, chunks_block=chunks_block
    )
    logger.info("[FLUXO/Grok] Enviando requisição para Grok API...")
    content = call_grok_chat(system_prompt, user, api_key)
    logger.info("[FLUXO/Grok] Resposta recebida (%d chars). Extraindo JSON...", len(content or ""))
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        raise ValueError("Resposta inválida do Grok: raiz do JSON deve ser objeto.")
    _validate_minimum_items(
        parsed,
        prompt_version=pv,
        enforce_minimum=enforce_minimum,
        allowed_theme_categories=allowed_theme_categories,
        brand_only=brand_only,
    )
    _save_grok_response_json(parsed, analysis_id=analysis_id)
    return parsed


READY_CUT_SYSTEM_PROMPT_BASE = """Você é um editor de conteúdo para redes sociais. Receberá a transcrição de um vídeo curto já editado (corte pronto).

Sua tarefa: retornar APENAS metadados para publicação:
- virality_score: 1-10 (potencial de viralização)
- title: título chamativo para YouTube/Shorts (45-100 caracteres). OBRIGATÓRIO incluir 1-3 emojis - aumenta engajamento.
- thumbnail_moment_timestamp: timestamp no formato MM:SS do melhor momento para capa (ex: "00:15")
- thumbnail_text: 2-4 palavras curtas para a capa (ex: "SEGREDO REVELADO")

Responda SOMENTE com JSON válido, sem markdown:
{"virality_score": 8, "title": "Título com emoji 🎯", "thumbnail_moment_timestamp": "00:12", "thumbnail_text": "MOMENTO CHAVE"}"""


def _ready_cuts_metadata_language_block(titles_language: str) -> str:
    lg = (titles_language or "pt").strip().lower()
    if lg == "en":
        return (
            "\n\nMANDATORY LANGUAGE: Write title and thumbnail_text ONLY in English (US). "
            "Do not use Portuguese or any other language."
        )
    return (
        "\n\nIDIOMA OBRIGATÓRIO: Escreva title e thumbnail_text APENAS em português brasileiro. "
        "Não use inglês nem outro idioma."
    )


def _ready_cuts_batch_transcripts_system_prompt(titles_language: str) -> str:
    lg = (titles_language or "pt").strip().lower()
    if lg == "en":
        lang_block = (
            "MANDATORY LANGUAGE: Write EVERY title in English (US) only — even if the transcript is in another language. "
            "Do not use Portuguese or any other language in the titles."
        )
    else:
        lang_block = (
            "IDIOMA OBRIGATÓRIO: Escreva TODOS os títulos apenas em português brasileiro — "
            "mesmo que a transcrição esteja em outro idioma. Não use inglês nos títulos."
        )
    return (
        "Você é um editor de redes sociais. Receberá um JSON com vários vídeos curtos (cortes), "
        "cada um com um índice (id) e a transcrição.\n\n"
        f"{lang_block}\n\n"
        "Tarefa: para CADA vídeo, invente UM título para YouTube Shorts (45–100 caracteres), chamativo.\n"
        "OBRIGATÓRIO: cada título deve incluir pelo menos 2 emojis relevantes (engajamento).\n\n"
        "Responda SOMENTE com JSON válido, sem markdown, neste formato exato:\n"
        '{"titles": {"0": "...", "1": "..."}}\n'
        "Use as chaves como string com o mesmo id de cada item."
    )


def _ready_cuts_batch_jobname_system_prompt(titles_language: str) -> str:
    lg = (titles_language or "pt").strip().lower()
    if lg == "en":
        lang_block = (
            "MANDATORY LANGUAGE: Write EVERY title in English (US) only. "
            "Do not use Portuguese or any other language."
        )
    else:
        lang_block = (
            "IDIOMA OBRIGATÓRIO: Escreva TODOS os títulos apenas em português brasileiro. "
            "Não use inglês nem outro idioma."
        )
    return (
        "Você é um editor de redes sociais. O usuário posta vários vídeos do MESMO nicho/tema; "
        "o nome geral do conjunto é informado abaixo.\n\n"
        f"{lang_block}\n\n"
        "Tarefa: crie exatamente N títulos ALTERNATIVOS entre si (distintos), para N vídeos desse segmento. "
        "Cada título: 45–100 caracteres, chamativo para Shorts.\n"
        "OBRIGATÓRIO: cada título deve ter pelo menos 2 emojis relevantes.\n\n"
        "Responda SOMENTE com JSON válido, sem markdown:\n"
        '{"titles": ["título 1", "título 2", ...]}'
    )


def analyze_ready_cut_metadata(
    transcript: str,
    duration_seconds: float,
    api_key: str | None = None,
    *,
    titles_language: str = "pt",
) -> dict:
    """
    Analisa vídeo já editado (corte pronto). Retorna apenas:
    virality_score, title, thumbnail_moment_timestamp, thumbnail_text.
    """
    if not (transcript or "").strip():
        return {
            "virality_score": 5,
            "title": "Vídeo",
            "thumbnail_moment_timestamp": "00:00",
            "thumbnail_text": "Vídeo",
        }
    duration_str = f"{int(duration_seconds // 60)}min {int(duration_seconds % 60)}s"
    user = f"""Transcrição do vídeo (duração: {duration_str}):

{transcript[:8000]}

Retorne JSON com: virality_score (1-10), title (SEMPRE com 1-3 emojis), thumbnail_moment_timestamp (MM:SS), thumbnail_text (2-4 palavras)."""
    system = READY_CUT_SYSTEM_PROMPT_BASE + _ready_cuts_metadata_language_block(titles_language)
    content = call_grok_chat(system, user, api_key)
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return {
            "virality_score": 5,
            "title": "Vídeo",
            "thumbnail_moment_timestamp": "00:00",
            "thumbnail_text": "Vídeo",
        }
    return {
        "virality_score": max(1, min(10, int(parsed.get("virality_score") or 5))),
        "title": (parsed.get("title") or "Vídeo")[:200],
        "thumbnail_moment_timestamp": (parsed.get("thumbnail_moment_timestamp") or "00:00").strip()[:16],
        "thumbnail_text": (parsed.get("thumbnail_text") or "Vídeo")[:80],
    }


def analyze_ready_cuts_batch_titles_from_transcripts(
    items: list[dict],
    api_key: str | None = None,
    *,
    titles_language: str = "pt",
) -> dict[str, str]:
    """
    items: [{"id": "0", "transcript": "..."}, ...]
    Retorna mapa id -> título.
    """
    if not items:
        return {}
    payload = json.dumps(
        [{"id": str(it.get("id", "")), "transcript": (it.get("transcript") or "")[:12000]} for it in items],
        ensure_ascii=False,
    )
    user = f"Dados dos vídeos (JSON):\n{payload}\n\nRetorne apenas o JSON com titles."
    system = _ready_cuts_batch_transcripts_system_prompt(titles_language)
    content = call_grok_chat(system, user, api_key)
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return {}
    titles = parsed.get("titles")
    if not isinstance(titles, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in titles.items():
        if v and str(v).strip():
            out[str(k)] = str(v).strip()[:200]
    return out


def analyze_ready_cuts_batch_titles_from_job_name(
    job_name: str,
    count: int,
    api_key: str | None = None,
    *,
    titles_language: str = "pt",
) -> list[str]:
    """Gera N títulos alternativos só com base no nome do job (sem transcrição)."""
    n = max(1, int(count))
    name = (job_name or "").strip() or "Conteúdo"
    user = f'Nome do conjunto / tema: "{name}"\n\nN = {n}\n\nCrie exatamente {n} títulos na lista.'
    system = _ready_cuts_batch_jobname_system_prompt(titles_language)
    content = call_grok_chat(system, user, api_key)
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return [f"{name} #{i+1}" for i in range(n)]
    titles = parsed.get("titles")
    if not isinstance(titles, list):
        return [f"{name} #{i+1}" for i in range(n)]
    cleaned = [str(t).strip()[:200] for t in titles if t and str(t).strip()]
    while len(cleaned) < n:
        cleaned.append(f"{name} #{len(cleaned)+1}")
    return cleaned[:n]
