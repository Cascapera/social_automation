"""Cliente Grok API (xAI) para análise de cortes virais.

Os prompts, o vocabulário que eles reaproveitam e a tabela de preço moravam aqui — 1.184
linhas em que a primeira função só aparecia na linha 1.236. Saíram no R-18 (D-09) para
`apps/auto_cuts/prompts/` e `grok_pricing.py`: são conteúdo e dado de negócio, com ciclo
de vida diferente do de um cliente HTTP. O que ficou é cliente, parsing e custo.
"""

import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from django.conf import settings

from apps.auto_cuts.prompts import (
    ALL_THEME_CATEGORIES,
    CHUNKS_PROMPT_TEMPLATE,
    CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL,
    CHUNKS_PROMPT_TEMPLATE_EDUCATIONAL_EN,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_EN,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_LONG_EN,
    CHUNKS_PROMPT_TEMPLATE_VIRAL_TRANSLATE,
    READY_CUT_SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_EDUCATIONAL,
    SYSTEM_PROMPT_EDUCATIONAL_EN,
    SYSTEM_PROMPT_VIRAL_EN,
    SYSTEM_PROMPT_VIRAL_LONG,
    SYSTEM_PROMPT_VIRAL_LONG_EN,
    SYSTEM_PROMPT_VIRAL_TRANSLATE,
)
from apps.auto_cuts.services.grok_pricing import GROK_PRICING
from apps.common.metrics import (
    grok_cost_usd_total,
    grok_request_duration_ms,
    grok_requests_total,
    grok_tokens_total,
)

logger = logging.getLogger(__name__)
THEME_CATEGORY_RETRY_THRESHOLD = 5

GROK_OPERATION_ANALYZE_CHUNKS = "analyze_chunks"
GROK_OPERATION_READY_CUT_METADATA = "ready_cut_metadata"
GROK_OPERATION_READY_CUTS_TITLES_FROM_TRANSCRIPTS = "ready_cuts_titles_from_transcripts"
GROK_OPERATION_READY_CUTS_TITLES_FROM_JOB_NAME = "ready_cuts_titles_from_job_name"

GROK_MODEL_ALIASES = {
    "grok-4-1-fast-reasoning-latest": "grok-4-1-fast-reasoning",
}

# Base URLs padrão por provedor (sobrescritas por LLM_BASE_URL se definido)
LLM_PROVIDER_DEFAULTS: dict[str, str] = {
    "xai": "https://api.x.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": "https://api.openai.com/v1",
}


























def _build_chunks_block(chunks: list[dict], lang: str = "pt") -> str:
    """Monta o bloco de transcrição do prompt.

    Com um único chunk — o caminho normal desde que a transcrição passou a ir inteira numa
    mensagem só — o texto vai limpo, sem rótulo. Marcador de bloco só faz sentido quando há
    mais de um: aí ele separa; sozinho, ele só sugere ao modelo uma fronteira que não
    existe, e corte que a atravesse pode deixar de ser proposto.
    """
    textos = [(chunk.get("text") or "").strip() for chunk in chunks]
    textos = [texto for texto in textos if texto]
    if len(textos) == 1:
        return textos[0]

    label = "BLOCK" if lang == "en" else "BLOCO"
    return "\n".join(f"--- {label} {i} ---\n{texto}\n" for i, texto in enumerate(textos, 1))


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
    min_candidates: int = 1,
    min_longs: int = 1,
) -> None:
    """
    Garante mínimos para prompts virais.
    Se não cumprir, levanta erro para o caller retentar.
    brand_only: quando True, não exige theme_category (conteúdo é de uma única marca).
    min_candidates/min_longs: limites mínimos esperados (injetados por analyze_chunks_in_one_request).
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

        if len(candidate_shorts) < min_candidates:
            msg = (
                f"Resposta abaixo do mínimo esperado: "
                f"candidate_shorts={len(candidate_shorts)} < {min_candidates}."
            )
            if enforce_minimum:
                raise ValueError(msg)
            logger.warning("[FLUXO/Grok] %s Seguindo com resposta parcial.", msg)
        if len(final_long_cuts) < min_longs:
            msg = (
                f"Resposta abaixo do mínimo esperado: "
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


def _normalize_grok_model_name(model_name: str) -> str:
    value = str(model_name or "").strip()
    if not value:
        return "unknown_model"
    return GROK_MODEL_ALIASES.get(value, value)


def _coerce_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    result = {}
    for attr in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
        "input_tokens_details",
        "output_tokens_details",
        "cached_tokens",
        "reasoning_tokens",
    ):
        if hasattr(value, attr):
            result[attr] = getattr(value, attr)
    return result


def _coerce_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _coerce_float(value, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def _get_grok_pricing() -> dict[str, dict[str, float]]:
    pricing = {
        model_name: {
            "input_per_1k": _coerce_float(config.get("input_per_1k")),
            "cached_input_per_1k": _coerce_float(
                config.get("cached_input_per_1k", config.get("input_per_1k"))
            ),
            "output_per_1k": _coerce_float(config.get("output_per_1k")),
        }
        for model_name, config in GROK_PRICING.items()
    }
    raw_override = settings.GROK_PRICING_JSON
    if not raw_override:
        return pricing
    try:
        parsed = json.loads(raw_override)
    except json.JSONDecodeError:
        logger.warning("[FLUXO/Grok] GROK_PRICING_JSON inválido; usando defaults.")
        return pricing
    if not isinstance(parsed, Mapping):
        logger.warning("[FLUXO/Grok] GROK_PRICING_JSON deve ser um objeto; usando defaults.")
        return pricing
    for model_name, config in parsed.items():
        if not isinstance(config, Mapping):
            continue
        normalized_model_name = _normalize_grok_model_name(str(model_name))
        pricing[normalized_model_name] = {
            "input_per_1k": _coerce_float(config.get("input_per_1k")),
            "cached_input_per_1k": _coerce_float(
                config.get("cached_input_per_1k", config.get("input_per_1k"))
            ),
            "output_per_1k": _coerce_float(config.get("output_per_1k")),
        }
    return pricing


def _extract_grok_usage(response) -> dict[str, int]:
    usage = _coerce_dict(getattr(response, "usage", None))
    input_tokens = _coerce_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    output_tokens = _coerce_int(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    total_tokens = _coerce_int(usage.get("total_tokens"))
    if not input_tokens and total_tokens and output_tokens:
        input_tokens = max(0, total_tokens - output_tokens)
    if not output_tokens and total_tokens and input_tokens:
        output_tokens = max(0, total_tokens - input_tokens)

    input_details = _coerce_dict(
        usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    )
    output_details = _coerce_dict(
        usage.get("completion_tokens_details") or usage.get("output_tokens_details")
    )
    cached_input_tokens = min(
        input_tokens,
        _coerce_int(input_details.get("cached_tokens")),
    )
    reasoning_tokens = _coerce_int(output_details.get("reasoning_tokens"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _calculate_grok_cost_usd(*, model: str, usage: Mapping[str, int]) -> float:
    pricing = _get_grok_pricing().get(_normalize_grok_model_name(model))
    if not pricing:
        return 0.0
    input_tokens = _coerce_int(usage.get("input_tokens"))
    output_tokens = _coerce_int(usage.get("output_tokens"))
    cached_input_tokens = min(
        input_tokens,
        _coerce_int(usage.get("cached_input_tokens")),
    )
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
    input_cost = (
        (uncached_input_tokens / 1000.0) * pricing["input_per_1k"]
        + (cached_input_tokens / 1000.0) * pricing["cached_input_per_1k"]
    )
    output_cost = (output_tokens / 1000.0) * pricing["output_per_1k"]
    return input_cost + output_cost


def _observe_grok_request_metrics(
    *,
    model: str,
    operation: str,
    duration_ms: float,
    usage: Mapping[str, int] | None = None,
) -> None:
    normalized_model = _normalize_grok_model_name(model)
    grok_requests_total.labels(
        model=normalized_model,
        operation=str(operation or "unknown_operation"),
    ).inc()
    grok_request_duration_ms.labels(model=normalized_model).observe(max(0.0, duration_ms))

    usage_payload = usage or {}
    input_tokens = _coerce_int(usage_payload.get("input_tokens"))
    output_tokens = _coerce_int(usage_payload.get("output_tokens"))
    if input_tokens:
        grok_tokens_total.labels(model=normalized_model, type="input").inc(input_tokens)
    if output_tokens:
        grok_tokens_total.labels(model=normalized_model, type="output").inc(output_tokens)

    cost_usd = _calculate_grok_cost_usd(model=normalized_model, usage=usage_payload)
    if cost_usd > 0.0:
        grok_cost_usd_total.labels(model=normalized_model).inc(cost_usd)


def _execute_grok_chat_completion(
    client,
    *,
    model_name: str,
    messages: list[dict],
    operation: str,
    response_format: dict | None = None,
):
    started_at = perf_counter()
    try:
        request_kwargs = {
            "model": model_name,
            "messages": messages,
        }
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        response = client.chat.completions.create(**request_kwargs)
    except Exception:
        _observe_grok_request_metrics(
            model=model_name,
            operation=operation,
            duration_ms=(perf_counter() - started_at) * 1000.0,
        )
        raise

    _observe_grok_request_metrics(
        model=model_name,
        operation=operation,
        duration_ms=(perf_counter() - started_at) * 1000.0,
        usage=_extract_grok_usage(response),
    )
    return response


def _build_llm_client(light: bool = False) -> tuple:
    """
    Constrói (OpenAI client, model_name, provider) a partir de variáveis de ambiente.

    Precedência:
      API key : LLM_API_KEY > XAI_API_KEY (deprecated, emite warning)
      Model   : LLM_MODEL_LIGHT (se light=True) ou LLM_MODEL > GROK_MODEL (deprecated)
      Base URL: LLM_BASE_URL > padrão do LLM_PROVIDER
    """
    from openai import OpenAI

    provider = settings.LLM_PROVIDER

    # API key
    api_key = settings.LLM_API_KEY
    if not api_key:
        api_key = settings.XAI_API_KEY
        if api_key:
            logger.warning(
                "[LLM] XAI_API_KEY deprecated; migrar para LLM_API_KEY no .env"
            )
    if not api_key:
        raise ValueError("LLM_API_KEY não configurada")

    # Model
    if light:
        model = settings.LLM_MODEL_LIGHT
    else:
        model = settings.LLM_MODEL
    if not model:
        model = settings.GROK_MODEL
        if model:
            logger.warning(
                "[LLM] GROK_MODEL deprecated; migrar para LLM_MODEL/LLM_MODEL_LIGHT no .env"
            )
    if not model:
        model = "grok-4-1-fast"

    # Base URL
    base_url = settings.LLM_BASE_URL
    if not base_url:
        base_url = LLM_PROVIDER_DEFAULTS.get(provider, LLM_PROVIDER_DEFAULTS["xai"])

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model, provider


def call_grok_chat(
    system: str,
    user: str,
    api_key: str | None = None,
    *,
    operation: str = "chat",
    light: bool = False,
) -> str:
    """Chama API LLM (OpenAI-compatible) e retorna o conteúdo da resposta."""
    client, model_name, provider = _build_llm_client(light=light)

    # api_key explícito (legado) substitui a key resolvida pelo builder
    if api_key:
        from openai import OpenAI as _OpenAI
        base_url = settings.LLM_BASE_URL or LLM_PROVIDER_DEFAULTS.get(
            settings.LLM_PROVIDER,
            LLM_PROVIDER_DEFAULTS["xai"],
        )
        client = _OpenAI(api_key=api_key, base_url=base_url)

    logger.info("[LLM] provider=%s model=%s operation=%s", provider, model_name, operation)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Força JSON object na resposta quando suportado pela API.
    try:
        resp = _execute_grok_chat_completion(
            client,
            model_name=model_name,
            messages=messages,
            operation=operation,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(
            "[LLM] response_format=json_object não suportado (%s). Tentando sem response_format.",
            e,
        )
        resp = _execute_grok_chat_completion(
            client,
            model_name=model_name,
            messages=messages,
            operation=operation,
        )

    # Detecta redirect de modelo pelo servidor
    actual_model = (getattr(resp, "model", None) or "").strip()
    if actual_model and actual_model != model_name:
        logger.warning(
            "[LLM] redirect detectado: solicitado=%s usado=%s — verificar configuração do provider",
            model_name,
            actual_model,
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
    if not settings.GROK_SAVE_RESPONSE_JSON:
        return
    try:
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
    max_shorts: int | None = None,
    max_longs: int | None = None,
) -> dict:
    """
    Analisa todos os chunks em uma única requisição.
    chunks: [{text, start_sec, end_sec, segments}, ...]
    prompt_version: viral, viral_long, educational, viral_en, viral_long_en, educational_en, viral_translate
    brand_only: quando True, theme_category é opcional (conteúdo para uma única marca).
    analysis_id: opcional; se GROK_SAVE_RESPONSE_JSON=1, salva a resposta em JSON com este id no nome.
    max_shorts/max_longs: quantos candidatos pedir. Sem valor, cai no teto de `settings` —
      o chamador é quem sabe o alvo do job, e é ele que aplica a margem.
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

    # Quantidade pedida: a do chamador, limitada pelo teto de env. O mínimo exigido na
    # validação sai daqui também, então pedir menos não faz a resposta ser recusada.
    llm_max_shorts = max(1, min(int(max_shorts or settings.LLM_MAX_SHORTS), settings.LLM_MAX_SHORTS))
    llm_max_longs = max(1, min(int(max_longs or settings.LLM_MAX_LONGS), settings.LLM_MAX_LONGS))
    if is_educational:
        if lang == "en":
            limit_block = (
                f"\n\n---\nFINAL LIMIT INSTRUCTION (overrides all previous instructions):\n"
                f"- ranked_shorts: return EXACTLY {llm_max_shorts} items.\n"
                f"- final_long_cuts: return EXACTLY {llm_max_longs} items."
            )
        else:
            limit_block = (
                f"\n\n---\nINSTRUÇÃO FINAL DE LIMITE (prevalece sobre qualquer instrução anterior):\n"
                f"- ranked_shorts: retorne EXATAMENTE {llm_max_shorts} itens.\n"
                f"- final_long_cuts: retorne EXATAMENTE {llm_max_longs} itens."
            )
    else:
        if lang == "en":
            limit_block = (
                f"\n\n---\nFINAL LIMIT INSTRUCTION (overrides all previous instructions):\n"
                f"- candidate_shorts: return EXACTLY {llm_max_shorts} items.\n"
                f"- final_long_cuts: return EXACTLY {llm_max_longs} items.\n"
                f"- ranked_shorts: ALWAYS return [] (legacy field — do not populate)."
            )
        else:
            limit_block = (
                f"\n\n---\nINSTRUÇÃO FINAL DE LIMITE (prevalece sobre qualquer instrução anterior):\n"
                f"- candidate_shorts: retorne EXATAMENTE {llm_max_shorts} itens.\n"
                f"- final_long_cuts: retorne EXATAMENTE {llm_max_longs} itens.\n"
                f"- ranked_shorts: SEMPRE retorne [] (campo legado — não preencher)."
            )
    user = user + limit_block

    logger.info(
        "[FLUXO/Grok] Enviando requisição (max_shorts=%d max_longs=%d)...",
        llm_max_shorts, llm_max_longs,
    )
    content = call_grok_chat(
        system_prompt,
        user,
        api_key,
        operation=GROK_OPERATION_ANALYZE_CHUNKS,
    )
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
        min_candidates=max(1, llm_max_shorts // 2),
        min_longs=max(1, llm_max_longs // 2),
    )
    from apps.auto_cuts.services.metadata_sanitizer import sanitize_payload
    sanitize_payload(parsed)
    _save_grok_response_json(parsed, analysis_id=analysis_id)
    return parsed




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
    content = call_grok_chat(
        system,
        user,
        api_key,
        operation=GROK_OPERATION_READY_CUT_METADATA,
        light=True,
    )
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return {
            "virality_score": 5,
            "title": "Vídeo",
            "thumbnail_moment_timestamp": "00:00",
            "thumbnail_text": "Vídeo",
        }
    from apps.auto_cuts.services.metadata_sanitizer import sanitize_clip
    sanitize_clip(parsed, clip_ref="ready_cut")
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
    content = call_grok_chat(
        system,
        user,
        api_key,
        operation=GROK_OPERATION_READY_CUTS_TITLES_FROM_TRANSCRIPTS,
        light=True,
    )
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return {}
    titles = parsed.get("titles")
    if not isinstance(titles, dict):
        return {}
    from apps.auto_cuts.services.metadata_sanitizer import sanitize_clip
    out: dict[str, str] = {}
    for k, v in titles.items():
        if v and str(v).strip():
            tmp = {"title": str(v).strip()}
            sanitize_clip(tmp, clip_ref=f"batch_title[{k}]")
            out[str(k)] = tmp["title"][:200]
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
    content = call_grok_chat(
        system,
        user,
        api_key,
        operation=GROK_OPERATION_READY_CUTS_TITLES_FROM_JOB_NAME,
        light=True,
    )
    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        return [f"{name} #{i+1}" for i in range(n)]
    titles = parsed.get("titles")
    if not isinstance(titles, list):
        return [f"{name} #{i+1}" for i in range(n)]
    from apps.auto_cuts.services.metadata_sanitizer import sanitize_clip
    cleaned = []
    for i, t in enumerate(titles):
        if t and str(t).strip():
            tmp = {"title": str(t).strip()}
            sanitize_clip(tmp, clip_ref=f"batch_jobname[{i}]")
            cleaned.append(tmp["title"][:200])
    while len(cleaned) < n:
        cleaned.append(f"{name} #{len(cleaned)+1}")
    return cleaned[:n]
