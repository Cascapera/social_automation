"""Templates da mensagem de usuário enviada ao modelo (refactor.md R-18 / D-09).

Um por variação, pareado com o `SYSTEM_PROMPT*` correspondente em `system.py`. São
strings de `.format()` — os campos `{context_block}`, `{chunks_block}` e afins são
preenchidos em `grok.analyze_chunks_in_one_request`.

⚠ Chave nova no template exige campo novo no `.format()`; chave removida sem ajustar o
chamador levanta `KeyError` só em runtime. Os hashes de `test_grok_prompts_integridade.py`
valem aqui também.
"""

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
- theme_category (OBRIGATÓRIO: use SOMENTE um dos valores listados em "CATEGORIAS DE TEMA PERMITIDAS NESTE JOB")
- emotion_type (funny/shocking/inspiring/controversial/story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 palavras fortes)
- suggested_description (250–600 chars, varie o estilo de abertura entre clips)
- tags (lista de 10–15 palavras-chave lowercase)

Somente em final_long_cuts (cortes longos), inclua também:
- chapters (3–8 itens com timestamps RELATIVOS ao início do clip; primeiro DEVE ser "00:00")
- suggested_first_comment (100–220 chars, comentário humano para pinar com CTA sutil)

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
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE",
      "suggested_description": "Você já passou por um climão desses no trabalho? Nesse corte o convidado conta em detalhes como descobriu que estava sendo demitido no meio da reunião — e a reação que virou piada interna da empresa. Se quiser entender o contexto completo, o episódio inteiro está linkado abaixo.",
      "tags": ["podcast", "história real", "trabalho", "demissão", "constrangimento", "corte viral", "bastidores", "reação", "história de trabalho", "situação inesperada"]
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE",
      "suggested_description": "Três detalhes que ninguém percebeu nesse momento: (1) a pausa antes da resposta, (2) o olhar pro relógio, (3) o pedido de água logo depois. Esse trecho do episódio mostra como uma pergunta simples pode mudar o tom da conversa inteira.",
      "tags": ["podcast", "entrevista", "reação", "momento tenso", "análise", "bastidores", "detalhe escondido", "corte viral", "climão", "história real"]
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 720,
      "virality_score": 88,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
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
      "reason": "por que viraliza",
      "suggested_description": "Neste bloco completo o convidado destrincha a estratégia que usou para escalar o negócio em 18 meses. Pontos abordados: (1) decisão inicial contra-intuitiva, (2) como validou a hipótese com pouco capital, (3) o erro que quase colocou tudo a perder, (4) o ponto de virada. Recomendo assistir até o fim — a conclusão muda a forma como você olha para crescimento.",
      "tags": ["empreendedorismo", "estratégia de crescimento", "negócios", "startup", "caso real", "decisão", "erro", "virada", "escalabilidade", "análise", "bastidores", "história empresarial"],
      "chapters": [
        {{"timestamp": "00:00", "title": "O ponto de partida"}},
        {{"timestamp": "01:42", "title": "A decisão contra-intuitiva"}},
        {{"timestamp": "04:15", "title": "Como validou com pouco capital"}},
        {{"timestamp": "07:30", "title": "O erro que quase derrubou tudo"}},
        {{"timestamp": "10:05", "title": "O ponto de virada"}}
      ],
      "suggested_first_comment": "Qual parte desse trecho você discorda? Eu achei a decisão do minuto 4 bem ousada. Se quiser ver o episódio completo, deixei linkado na descrição 👇"
    }}
  ]
}}

Regras finais:
- candidate_shorts deve ter entre 30 e 50 itens.
- final_long_cuts deve ter exatamente 10 itens.
- ranked_shorts pode vir vazio ([]).
- Todo texto (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, suggested_description, suggested_first_comment, tags, chapters, etc.) em português brasileiro."""

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
- theme_category (OBRIGATÓRIO: use SOMENTE um dos valores listados em "CATEGORIAS DE TEMA PERMITIDAS NESTE JOB")
- emotion_type (funny/shocking/inspiring/controversial/story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 palavras fortes)
- suggested_description (250–600 chars, varie o estilo de abertura entre clips)
- tags (lista de 10–15 palavras-chave lowercase)

Somente em final_long_cuts (cortes longos), inclua também:
- chapters (3–8 itens com timestamps RELATIVOS ao início do clip; primeiro DEVE ser "00:00")
- suggested_first_comment (100–220 chars, comentário humano para pinar com CTA sutil)

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
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE",
      "suggested_description": "Você já passou por um climão desses no trabalho? Nesse corte o convidado conta em detalhes como descobriu que estava sendo demitido no meio da reunião — e a reação que virou piada interna da empresa. Se quiser entender o contexto completo, o episódio inteiro está linkado abaixo.",
      "tags": ["podcast", "história real", "trabalho", "demissão", "constrangimento", "corte viral", "bastidores", "reação", "história de trabalho", "situação inesperada"]
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "história constrangedora no trabalho",
      "hook_sentence": "frase mais impactante",
      "suggested_title": "Título forte 🎯",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "PALAVRA FORTE",
      "suggested_description": "Três detalhes que ninguém percebeu nesse momento: (1) a pausa antes da resposta, (2) o olhar pro relógio, (3) o pedido de água logo depois. Esse trecho do episódio mostra como uma pergunta simples pode mudar o tom da conversa inteira.",
      "tags": ["podcast", "entrevista", "reação", "momento tenso", "análise", "bastidores", "detalhe escondido", "corte viral", "climão", "história real"]
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "MM:SS",
      "end_timestamp": "MM:SS",
      "duration_seconds": 720,
      "virality_score": 88,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
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
      "reason": "por que viraliza",
      "suggested_description": "Neste bloco completo o convidado destrincha a estratégia que usou para escalar o negócio em 18 meses. Pontos abordados: (1) decisão inicial contra-intuitiva, (2) como validou a hipótese com pouco capital, (3) o erro que quase colocou tudo a perder, (4) o ponto de virada. Recomendo assistir até o fim — a conclusão muda a forma como você olha para crescimento.",
      "tags": ["empreendedorismo", "estratégia de crescimento", "negócios", "startup", "caso real", "decisão", "erro", "virada", "escalabilidade", "análise", "bastidores", "história empresarial"],
      "chapters": [
        {{"timestamp": "00:00", "title": "O ponto de partida"}},
        {{"timestamp": "01:42", "title": "A decisão contra-intuitiva"}},
        {{"timestamp": "04:15", "title": "Como validou com pouco capital"}},
        {{"timestamp": "07:30", "title": "O erro que quase derrubou tudo"}},
        {{"timestamp": "10:05", "title": "O ponto de virada"}}
      ],
      "suggested_first_comment": "Qual parte desse trecho você discorda? Eu achei a decisão do minuto 4 bem ousada. Se quiser ver o episódio completo, deixei linkado na descrição 👇"
    }}
  ]
}}

Regras finais:
- candidate_shorts deve ter entre 30 e 50 itens.
- final_long_cuts deve ter exatamente 10 itens.
- ranked_shorts pode vir vazio ([]).
- Todo texto (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, suggested_description, suggested_first_comment, tags, chapters, etc.) em português brasileiro."""

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
- suggested_description (250–600 chars, varie o estilo de abertura entre clips)
- tags (10–15 palavras-chave lowercase)

Somente em final_long_cuts, inclua também:
- chapters (3–8 itens; primeiro timestamp "00:00", relativos ao início do clip)
- suggested_first_comment (100–220 chars, comentário humano para pinar com CTA sutil)

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
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "IDEIA CENTRAL",
      "suggested_description": "Como você decide quando vale a pena arriscar no investimento? Este trecho apresenta um método simples em três passos para avaliar o risco antes de mover o dinheiro. Exemplos reais e aplicação prática ao final.",
      "tags": ["finanças", "investimento", "educação financeira", "estratégia", "risco", "decisão financeira", "guia prático", "caso real", "análise", "didático"]
    }}
  ],
  "final_long_cuts": [
    {{
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 18,
      "title_suggestion": "Título informativo 📚",
      "reason": "valor didático",
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "RESUMO FORTE",
      "suggested_description": "Aula completa sobre alocação de patrimônio em três cenários distintos. Pontos abordados: (1) base defensiva, (2) diversificação internacional, (3) proteção cambial, (4) rebalanceamento anual. Material feito para quem está começando e quer uma visão estruturada.",
      "tags": ["educação financeira", "alocação de ativos", "investimento", "patrimônio", "diversificação", "renda fixa", "renda variável", "planejamento", "estratégia", "guia completo", "aula", "didático"],
      "chapters": [
        {{"timestamp": "00:00", "title": "Introdução e contexto"}},
        {{"timestamp": "02:40", "title": "Base defensiva"}},
        {{"timestamp": "07:10", "title": "Diversificação internacional"}},
        {{"timestamp": "12:25", "title": "Proteção cambial"}},
        {{"timestamp": "15:40", "title": "Rebalanceamento anual"}}
      ],
      "suggested_first_comment": "Qual desses pontos você aplica hoje na sua carteira? Curioso pra ouvir quem faz diferente. O material completo com os números exatos está no episódio inteiro linkado aqui."
    }}
  ]
}}

Máximo: 10–15 cortes curtos (2–3 min), 3 cortes longos."""

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
- theme_category (REQUIRED: use ONLY one of the values listed in "ALLOWED THEME CATEGORIES FOR THIS JOB")
- emotion_type (funny / shocking / inspiring / controversial / story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 powerful words)
- suggested_description (250–600 chars, unique per clip, vary structure: question / bold statement / bullet list)
- tags (10–15 lowercase keywords, mix generic and specific)

For clips in final_long_cuts, ALSO include:
- chapters: 3–8 chapters like [{{"timestamp":"MM:SS","title":"..."}}], first ALWAYS at "00:00"
- suggested_first_comment (100–220 chars, as if written by the channel owner, natural tone with soft CTA)

Additional rules:
- suggested_title and title_suggestion: REQUIRED 1–3 emojis in ALL titles (shorts and longs). Never return a title without emojis.
- suggested_title: 45–100 characters with 1–3 relevant emojis.
- thumbnail_text: 2–4 words (max 28 chars), preferably uppercase.
- All text (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, suggested_description, tags, chapters, suggested_first_comment, etc.) MUST be in English.

Respond ONLY with valid JSON:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "suggested_description": "Ever wondered what it feels like to be fired live on stage? In this clip he shares the exact moment he realized the cameras were rolling and his career had just changed forever. A raw, funny, and slightly painful story about how public embarrassment can be a turning point.",
      "tags": ["fired live","embarrassing story","workplace fail","career turn","public humiliation","viral clip","real story","work moment","stage fail","shorts","funny","life lesson"]
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "suggested_description": "A short version of one of the most uncomfortable moments of his career, told with humor and honesty. Watch and tell me in the comments: would you handle it the same way?",
      "tags": ["fired live","embarrassing moment","career","work story","viral","shorts","funny clip","real story","stage","turning point","life"]
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:42:10",
      "end_timestamp": "00:53:40",
      "duration_seconds": 690,
      "virality_score": 88,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
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
      "suggested_description": "In this chapter he walks through the exact decision that flipped his career upside down. We cover the context before the choice, the fears that almost stopped him, the mindset shift that made it possible, and the outcome that followed. If you are stuck at a crossroads, this one is for you.",
      "tags": ["career decision","life change","turning point","mindset shift","courage","real story","long form","interview","professional growth","personal development","motivation","career advice","inspiration","lessons"],
      "chapters": [
        {{"timestamp":"00:00","title":"Intro: the night before the decision"}},
        {{"timestamp":"02:15","title":"The fear that almost stopped him"}},
        {{"timestamp":"05:40","title":"The mindset shift"}},
        {{"timestamp":"08:10","title":"What happened next"}},
        {{"timestamp":"10:30","title":"Lessons and takeaways"}}
      ],
      "suggested_first_comment": "What would you have done in his place? Leave your answer in the comments — I read every single one and I'm already picking a few to discuss on the next video."
    }}
  ]
}}

Final constraints:
- candidate_shorts must contain between 30 and 50 items.
- final_long_cuts must contain exactly 10 items.
- ranked_shorts may be empty ([])."""

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
- theme_category (REQUIRED: use ONLY one of the values listed in "ALLOWED THEME CATEGORIES FOR THIS JOB")
- emotion_type (funny / shocking / inspiring / controversial / story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 powerful words)
- suggested_description (250–600 chars, unique per clip, vary structure: question / bold statement / bullet list)
- tags (10–15 lowercase keywords, mix generic and specific)

For clips in final_long_cuts, ALSO include:
- chapters: 3–8 chapters like [{{"timestamp":"MM:SS","title":"..."}}], first ALWAYS at "00:00"
- suggested_first_comment (100–220 chars, as if written by the channel owner, natural tone with soft CTA)

Additional rules:
- suggested_title and title_suggestion: REQUIRED 1–3 emojis in ALL titles (shorts and longs). Never return a title without emojis.
- suggested_title: 45–100 characters with 1–3 relevant emojis.
- thumbnail_text: 2–4 words (max 28 chars), preferably uppercase.
- Shorts: target duration 90–160 seconds (do NOT use 30–60s clips in this mode).
- All text (suggested_title, thumbnail_text, hook_sentence, main_topic, reason, suggested_description, tags, chapters, suggested_first_comment, etc.) MUST be in English.

Respond ONLY with valid JSON:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:17:22",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "suggested_description": "Two full minutes of one of the most awkward career stories ever told. He explains the warning signs he ignored, the moment he realized it was over, and the reaction that followed. Perfect watch if you like raw real-life stories that feel like a mini documentary.",
      "tags": ["fired live","embarrassing story","workplace fail","career turn","public humiliation","long short","real story","work moment","stage fail","viral clip","funny","life lesson","interview"]
    }}
  ],
  "ranked_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:17:22",
      "duration_seconds": 120,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "suggested_description": "A slightly longer version of the fired-live story, with the full lead-up and the reaction he had the next day. Watch till the end — tell me what you would have done differently in the comments.",
      "tags": ["fired live","embarrassing moment","career","work story","viral","long short","funny clip","real story","stage","turning point","life","interview"]
    }}
  ],
  "final_long_cuts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:42:10",
      "end_timestamp": "00:53:40",
      "duration_seconds": 690,
      "virality_score": 88,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
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
      "suggested_description": "A full chapter about the decision that flipped his career upside down: what led up to it, the fears he had to fight, the mindset shift that unlocked the move, and the aftermath. If you are facing a crossroads, save this one.",
      "tags": ["career decision","life change","turning point","mindset shift","courage","real story","long form","interview","professional growth","personal development","motivation","career advice","inspiration","lessons"],
      "chapters": [
        {{"timestamp":"00:00","title":"Intro: the night before the decision"}},
        {{"timestamp":"02:15","title":"The fear that almost stopped him"}},
        {{"timestamp":"05:40","title":"The mindset shift"}},
        {{"timestamp":"08:10","title":"What happened next"}},
        {{"timestamp":"10:30","title":"Lessons and takeaways"}}
      ],
      "suggested_first_comment": "What would you have done in his place? Leave your answer in the comments — I read every single one and I'm already picking a few to discuss on the next video."
    }}
  ]
}}

Final constraints:
- candidate_shorts must contain between 30 and 50 items.
- final_long_cuts must contain exactly 10 items.
- ranked_shorts may be empty ([])."""

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
- theme_category (REQUIRED: use ONLY one of the values listed in "ALLOWED THEME CATEGORIES FOR THIS JOB")
- emotion_type (funny / shocking / inspiring / controversial / story)
- main_topic
- suggested_title
- hook_sentence
- thumbnail_moment_timestamp
- thumbnail_text (2–4 powerful words)
- suggested_description (250–600 chars, unique per clip, vary structure: question / bold statement / bullet list) — written in English (will describe the clip for the English audience)
- tags (10–15 lowercase keywords, mix generic and specific) — in English
- subtitle_segments_pt (REQUIRED): array of {{"start": float, "end": float, "text": "PT translation"}}

For clips in final_long_cuts, ALSO include:
- chapters: 3–8 chapters like [{{"timestamp":"MM:SS","title":"..."}}], first ALWAYS at "00:00" — titles in English
- suggested_first_comment (100–220 chars, as if written by the channel owner, natural tone with soft CTA) — in English

Additional rules:
- suggested_title and title_suggestion: REQUIRED 1–3 emojis in ALL titles (shorts and longs). Never return a title without emojis.
- suggested_title: 45–100 characters with 1–3 relevant emojis.
- thumbnail_text: 2–4 words (max 28 chars), preferably uppercase.
- suggested_description, tags, chapters, suggested_first_comment are ALL in English (same as suggested_title). Only subtitle_segments_pt contains Brazilian Portuguese.

Respond ONLY with valid JSON:
{{
  "candidate_shorts": [
    {{
      "clip_number": 1,
      "start_timestamp": "00:15:22",
      "end_timestamp": "00:16:05",
      "duration_seconds": 43,
      "virality_score": 96,
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "suggested_description": "Ever wondered what it feels like to be fired live on stage? In this clip he shares the exact moment he realized the cameras were rolling and his career had just changed forever. A raw, funny, and slightly painful story about how public embarrassment can be a turning point.",
      "tags": ["fired live","embarrassing story","workplace fail","career turn","public humiliation","viral clip","real story","work moment","stage fail","shorts","funny","life lesson"],
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
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "emotion_type": "funny",
      "main_topic": "embarrassing story at work",
      "hook_sentence": "And that was the moment I realized I had been fired live on stage.",
      "suggested_title": "He Got Fired In The Most Embarrassing Way 😱",
      "thumbnail_moment_timestamp": "00:15:34",
      "thumbnail_text": "FIRED LIVE",
      "suggested_description": "A short version of one of the most uncomfortable moments of his career, told with humor and honesty. Watch and tell me in the comments: would you handle it the same way?",
      "tags": ["fired live","embarrassing moment","career","work story","viral","shorts","funny clip","real story","stage","turning point","life"],
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
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
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
      "suggested_description": "In this chapter he walks through the exact decision that flipped his career upside down. We cover the context before the choice, the fears that almost stopped him, the mindset shift that made it possible, and the outcome that followed. If you are stuck at a crossroads, this one is for you.",
      "tags": ["career decision","life change","turning point","mindset shift","courage","real story","long form","interview","professional growth","personal development","motivation","career advice","inspiration","lessons"],
      "chapters": [
        {{"timestamp":"00:00","title":"Intro: the night before the decision"}},
        {{"timestamp":"02:15","title":"The fear that almost stopped him"}},
        {{"timestamp":"05:40","title":"The mindset shift"}},
        {{"timestamp":"08:10","title":"What happened next"}},
        {{"timestamp":"10:30","title":"Lessons and takeaways"}}
      ],
      "suggested_first_comment": "What would you have done in his place? Leave your answer in the comments — I read every single one and I'm already picking a few to discuss on the next video.",
      "subtitle_segments_pt": [{{"start": 2530.0, "end": 2535.2, "text": "Uma decisão mudou tudo na minha carreira"}}]
    }}
  ]
}}

Final constraints:
- candidate_shorts must contain between 30 and 50 items.
- final_long_cuts must contain exactly 10 items.
- ranked_shorts may be empty ([]).
- EVERY clip MUST have subtitle_segments_pt with the Portuguese translation of transcript segments in that time range."""

CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN = """{context_block}Video transcription divided into blocks (with timestamps):

{chunks_block}

---

Tasks (respond in ONE JSON response):

1. RANKED_SHORTS: Identify 10–15 EDUCATIONAL short clips (2–3 min each, 120–180 sec). Prioritize blocks that explain a complete concept. Rank by didactic value. IMPORTANT: Each cut must have beginning, middle and end. Never cut in the middle of an explanation.

2. FINAL_LONG_CUTS: Assemble 1–3 long cuts (20–40 min) combining narrative blocks with natural flow. Suggest informative title for each.

Titles: informative and professional. REQUIRED to include 1–3 emojis in all (title and title_suggestion). Avoid sensationalism.
All text (title, title_suggestion, thumbnail_text, hook, reason, suggested_description, tags, chapters, suggested_first_comment, etc.) MUST be in English.
For every cut, include:
- thumbnail_moment_timestamp (real timestamp inside the cut)
- thumbnail_text (2–4 short words for cover text)
- suggested_description (250–600 chars, unique per clip, vary structure: question / bold statement / bullet list)
- tags (10–15 lowercase keywords, mix generic and specific)

For clips in final_long_cuts, ALSO include:
- chapters: 3–8 chapters like [{{"timestamp":"MM:SS","title":"..."}}], first ALWAYS at "00:00"
- suggested_first_comment (100–220 chars, as if written by the channel owner, natural tone with soft CTA)

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
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "CORE IDEA",
      "suggested_description": "In this short cut we walk through the core idea of portfolio allocation in under three minutes. I explain why diversification matters, where most investors get it wrong, and a simple rule you can apply to your own setup today. Save it if you want to come back later.",
      "tags": ["portfolio allocation","diversification","investing basics","personal finance","wealth building","long term investing","investor mistakes","finance tips","strategy","money","asset allocation","financial education"]
    }}
  ],
  "final_long_cuts": [
    {{
      "start": "MM:SS",
      "end": "MM:SS",
      "duration_min": 18,
      "title_suggestion": "Informative title 📚",
      "reason": "didactic value",
      "theme_category": "<UM_DOS_CODIGOS_PERMITIDOS>",
      "thumbnail_moment_timestamp": "MM:SS",
      "thumbnail_text": "KEY LESSON",
      "suggested_description": "Full chapter on how to structure your investment portfolio for long-term growth. We cover the basics of asset allocation, the role of risk tolerance, a practical example with real numbers, and the mistakes that cost most investors 10+ years of compounding. Practical and friendly.",
      "tags": ["investment strategy","asset allocation","long term investing","personal finance","wealth","financial education","investor mistakes","compounding","risk tolerance","portfolio","finance class","money management","passive income","financial planning"],
      "chapters": [
        {{"timestamp":"00:00","title":"Intro and context"}},
        {{"timestamp":"02:30","title":"Basics of asset allocation"}},
        {{"timestamp":"06:10","title":"Risk tolerance in practice"}},
        {{"timestamp":"10:45","title":"Practical example"}},
        {{"timestamp":"14:20","title":"Common mistakes to avoid"}}
      ],
      "suggested_first_comment": "Which of these points surprised you the most? Drop it in the comments — I'm collecting questions for a Q&A video in a couple of weeks."
    }}
  ]
}}

Max: 10–15 short cuts (2–3 min), 3 long cuts."""
