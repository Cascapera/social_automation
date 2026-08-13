"""Tabela de preço por modelo de LLM (refactor.md R-18 / D-09).

Estava dentro de `grok.py`, entre os prompts e o cliente HTTP. É dado de negócio com ciclo
próprio: muda quando o fornecedor mexe no preço, não quando o código muda.

Valores em **USD por 1 milhão de tokens**. `grok._get_grok_pricing()` permite sobrepor por
variável de ambiente; esta tabela é o default.
"""

GROK_PRICING = {
    # xAI
    "grok-4-1-fast": {
        "input_per_1k": 0.0002,
        "cached_input_per_1k": 0.00005,
        "output_per_1k": 0.0004,
    },
    # descontinuado em maio/2026 — mantido para métricas históricas
    "grok-4-1-fast-reasoning": {
        "input_per_1k": 0.0002,
        "cached_input_per_1k": 0.00005,
        "output_per_1k": 0.0005,
    },
    # destino atual do redirect xAI — input $1.25/M, output $2.50/M
    "grok-4.3": {
        "input_per_1k": 0.00125,
        "cached_input_per_1k": 0.00125,
        "output_per_1k": 0.0025,
    },
    # Google — input $0.10/M, output $0.40/M (gemini-2.0-flash descontinuado p/ contas novas)
    "gemini-2.5-flash": {
        "input_per_1k": 0.0001,
        "cached_input_per_1k": 0.0001,
        "output_per_1k": 0.0004,
    },
    "gemini-2.0-flash": {
        "input_per_1k": 0.0001,
        "cached_input_per_1k": 0.0001,
        "output_per_1k": 0.0004,
    },
    # OpenAI
    "gpt-4o-mini": {
        "input_per_1k": 0.00015,
        "cached_input_per_1k": 0.000075,
        "output_per_1k": 0.0006,
    },
    "gpt-4o": {
        "input_per_1k": 0.0025,
        "cached_input_per_1k": 0.00125,
        "output_per_1k": 0.01,
    },
}
