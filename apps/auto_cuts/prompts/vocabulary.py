"""Vocabulário e blocos de regra reaproveitados pelos prompts (refactor.md R-18 / D-09).

São os pedaços que os `SYSTEM_PROMPT*` concatenam, mais as listas de apoio. Ficam
separados dos prompts porque mudam por motivo próprio: uma regra anti-automação nova vale
para os sete prompts de uma vez.

⚠ `CTR_WORDS_PT`, `CTR_WORDS_EN`, `FORBIDDEN_WORDS_PT` e `FORBIDDEN_WORDS_EN` **não são
lidos por nenhum código** hoje — foram escritos e nunca ligados a lugar nenhum. Vieram
junto na mudança de casa em vez de serem apagados, porque são conteúdo editorial e quem os
escreveu pode ter uma intenção que o código não registra. Decidir entre usar ou remover é
item próprio; ver L-9 no refactor.md.
"""

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

# Regras anti-automação: descrição dinâmica + tags + capítulos + primeiro comentário.
# Concatenadas aos SYSTEM_PROMPT* para reduzir padrões repetitivos que disparam detecção
# de automação do YouTube. Shorts recebem só description+tags; longs ganham chapters e
# primeiro comentário pinado.
ANTI_AUTOMATION_RULES_PT = """REGRAS DE DESCRIÇÃO E TAGS (anti-automação):
- suggested_description: 250–600 caracteres únicos por clip, em português brasileiro. Varie o estilo de abertura entre clips da mesma resposta: (1) pergunta aberta; (2) afirmação forte; (3) lista curta de pontos abordados. Não copie o título. Não use hashtags.
- tags: 10–15 palavras-chave em lowercase, específicas ao conteúdo do clip (sem # e sem ponto final). Misture termos curtos (1 palavra) e long-tail (2–4 palavras).
- Para cortes em final_long_cuts (longos), inclua também:
  - chapters: 3–8 capítulos como [{"timestamp":"MM:SS","title":"..."}]. Timestamps RELATIVOS ao início do clip (o primeiro capítulo DEVE ser "00:00"). Títulos curtos (máx 60 chars).
  - suggested_first_comment: 100–220 caracteres de texto humano/autoral para pinar como primeiro comentário. Comece com pergunta OU observação pessoal e termine com um CTA sutil (convidar a comentar/assistir completo). Sem hashtags; no máximo 2 emojis."""

ANTI_AUTOMATION_RULES_EN = """DESCRIPTION AND TAGS RULES (anti-automation):
- suggested_description: 250–600 unique characters per clip, in English. Vary the opening style across clips: (1) open question; (2) strong statement; (3) short list of points covered. Never reuse formulas between clips. Never copy the title. Never use hashtags.
- tags: 10–15 lowercase keywords specific to the clip content (no # and no trailing dot). Mix short (1 word) and long-tail (2–4 words) terms.
- For clips in final_long_cuts (long cuts), also include:
  - chapters: 3–8 chapters as [{"timestamp":"MM:SS","title":"..."}]. Timestamps RELATIVE to the clip start (first chapter MUST be "00:00"). Short titles (max 60 chars).
  - suggested_first_comment: 100–220 characters of human/authorial text to pin as the first comment. Open with a question OR personal observation and end with a subtle CTA (invite comment/watch full). No hashtags; at most 2 emojis."""

METADATA_SAFETY_RULES_PT = """
REGRA DE METADADOS (CRÍTICA — leia antes de gerar qualquer título):
O vídeo PODE conter palavrões, linguajar adulto ou conteúdo sexual no áudio — isso é irrelevante para a seleção dos cortes. Mas suggested_title, thumbnail_text, hook_sentence, suggested_description, tags e suggested_first_comment são escaneados automaticamente pelo YouTube e impactam diretamente distribuição, monetização e CTR. Nesses campos, NUNCA reproduza linguajar explícito, independente do que está no vídeo. Parafraseie capturando a emoção sem reproduzir o termo.

Exemplos de contraste:
❌ "Ele transou com a chefe e levou uma voadora"
✅ "Ele se envolveu com a chefe e tudo saiu do controle 😱"
❌ "F*da-se, eu largo tudo e vou embora"
✅ "Ele larga tudo, para tudo e vai embora de uma vez 🔥"
❌ "O momento em que ela fez uma merda ao vivo"
✅ "O momento em que tudo desmoronou ao vivo"
❌ "Esse cara é um arrombado completo"
✅ "Esse cara passou dos limites e todo mundo ficou chocado"

Termos que NUNCA devem aparecer nos metadados: palavrões (porra, caralho, merda, foda, bosta, filha da puta), termos sexuais (sexo, transar, putaria, pornografia, orgia, prostituta), termos com restrição automática (estupro, terrorismo, extremismo, racismo, ódio, suicídio, execução). Substitua pela emoção: chocante, absurdo, explosivo, sem filtro, inacreditável, polêmico, pesado, tenso, limite.

Títulos sem palavrão tendem a ter CTR igual ou superior porque o algoritmo distribui mais amplamente."""

METADATA_SAFETY_RULES_EN = """
METADATA RULE (CRITICAL — read before generating any title):
The video MAY contain profanity, adult language, or sexual content in the audio — that is irrelevant to the clip selection itself. But suggested_title, thumbnail_text, hook_sentence, suggested_description, tags, and suggested_first_comment are automatically scanned by YouTube and directly impact distribution, monetization, and CTR. In these fields, NEVER reproduce explicit language, regardless of what is in the video. Rephrase to capture the emotion without using the term.

Contrast examples:
❌ "He f*cked the boss and got punched"
✅ "He crossed the line with his boss and everything exploded 😱"
❌ "That guy is a complete a**hole"
✅ "That guy went too far and nobody could believe it"
❌ "The moment she screwed up live on air"
✅ "The moment everything fell apart live on air"
❌ "He just said f*ck it and walked away"
✅ "He said enough, walked away, and shocked everyone 🔥"

Terms that must NEVER appear in metadata: profanity (fuck, shit, asshole, bitch), sexual terms (porn, sex tape, orgy, explicit sex, cock, pussy, prostitute), restricted terms (rape, terrorism, extremism, racism, hate, suicide, execution). Replace with the emotion: shocking, absurd, explosive, unfiltered, unbelievable, controversial, heavy, intense.

Titles without profanity achieve equal or better CTR because the algorithm distributes them more broadly."""


# Sinais que o próprio narrador dá de onde está o melhor corte. Antes disso, o prompt tratava
# toda a transcrição como texto plano: um "presta atenção nessa parte" tinha o mesmo peso que
# qualquer outra frase, embora seja a única marcação de valor que vem de quem conhece o
# conteúdo. As duas regras de direção existem porque o marcador tanto anuncia o que vem
# quanto resume o que passou, e cortar para o lado errado entrega o aviso sem a explicação.
AUTHOR_CUE_RULES_PT = """SINAIS DO PRÓPRIO AUTOR (prioridade alta na seleção):
Quem está falando às vezes marca sozinho onde está o melhor momento. Trate esse marcador como indício forte de corte bom e priorize o trecho que ele aponta.

Exemplos de marcador: "isso aqui é importante", "presta atenção nessa parte", "guarda essa frase", "anota isso", "escreve isso aí", "não esquece disso", "esse trecho daria um bom corte", "isso aqui é um corte", "o que eu vou falar agora muda tudo", "essa é a parte que ninguém entende", "volta aqui e escuta de novo", "se você for lembrar de uma coisa, lembra dessa".

Regras:
- Marcador que aponta PARA FRENTE ("presta atenção no que eu vou explicar"): o conteúdo vem DEPOIS dele. Comece o corte no marcador ou poucos segundos antes, e termine na conclusão do raciocínio.
- Marcador que aponta PARA TRÁS ("isso que eu acabei de falar é o mais importante"): o conteúdo vem ANTES dele. Comece o corte no início da explicação que o marcador está resumindo, e inclua o marcador no fim.
- O marcador diz ONDE cortar, não O QUE dizer. Nunca use a frase do marcador em suggested_title, thumbnail_text ou hook_sentence: o gancho tem que ser o conteúdo em si, nunca o aviso de que o conteúdo é importante.
- Marcador não salva trecho fraco. Se o que ele aponta não se sustenta sozinho fora do episódio, dê nota baixa mesmo assim.
- Vale para qualquer formato: podcast, entrevista, aula, leitura de livro, narração.
- author_cue: havendo marcador, copie a frase como ela aparece na transcrição, no máximo 120 caracteres. Não havendo, devolva "".

"""

AUTHOR_CUE_RULES_EN = """SIGNALS FROM THE SPEAKER (high priority in selection):
The person speaking sometimes marks the best moment themselves. Treat that marker as strong evidence of a good clip and prioritize the passage it points to.

Marker examples: "this part is important", "pay attention to this", "remember this line", "write this down", "don't forget this", "this would make a great clip", "this right here is a clip", "what I'm about to say changes everything", "this is the part nobody understands", "go back and listen to this again", "if you remember one thing, remember this".

Rules:
- Marker pointing FORWARD ("pay attention to what I'm about to explain"): the content comes AFTER it. Start the clip at the marker or a few seconds before, and end at the natural conclusion of the reasoning.
- Marker pointing BACKWARD ("what I just said is the most important thing"): the content comes BEFORE it. Start the clip at the beginning of the explanation the marker is summarizing, and include the marker at the end.
- The marker tells you WHERE to cut, not WHAT to say. Never use the marker phrase in suggested_title, thumbnail_text or hook_sentence: the hook must be the content itself, never the announcement that the content matters.
- A marker does not rescue a weak passage. If what it points to cannot stand alone outside the episode, score it low anyway.
- Applies to any format: podcast, interview, lecture, book reading, narration.
- author_cue: when a marker exists, copy the phrase as it appears in the transcript, max 120 characters. When there is none, return "".

"""
