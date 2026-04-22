"""Sanitiza metadados gerados por LLM antes de salvar no banco.

Aplica substituicoes de termos problematicos (palavroes, termos sexuais/violentos)
nos campos que ficam visiveis na plataforma: titulo, thumbnail, descricao, tags.
Nunca levanta excecao — falha silenciosa com log de warning.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Substitutions: (pattern, replacement, context)
# context = "title" aplica em suggested_title e thumbnail_text (mais restrito)
# context = "all"   aplica em todos os campos de metadados
#
# Ordem importa: termos mais longos primeiro para evitar substituicoes parciais.
# ---------------------------------------------------------------------------

# fmt: off
_SUBSTITUTIONS: list[tuple[str, str, str]] = [
    # --- Termos sexuais PT (mais restritivos — context=all) ---
    (r"\bfutuc(ar|ou|ando|ado)\b",       "envolvimento",           "all"),
    (r"\btrans(?:ar|ou|ando|ado|ava)\b",   "se envolver",            "all"),
    (r"\btrep(ar|ou|ando|ado)\b",         "se envolver",            "all"),
    (r"\bfoder\b",                        "acontecer",              "all"),
    (r"\bfod(eu|ido|endo)\b",             "ferrou",                 "all"),
    (r"\bputaria\b",                      "escandalo",              "all"),
    (r"\bpornografia\b",                  "conteudo adulto",        "all"),
    (r"\bporno\b",                        "conteudo adulto",        "all"),
    (r"\bprostituicao\b",                 "escandalo intimo",       "all"),
    (r"\bprostitucion\b",                 "escandalo intimo",       "all"),
    (r"\bputeiro\b",                      "local proibido",         "all"),
    (r"\borgia\b",                        "situacao intima",        "all"),
    (r"\bsexo explicito\b",               "conteudo +18",           "all"),
    (r"\bbu[xc]eta\b",                    "situacao",               "all"),
    (r"\bp[iy][ck]a\b",                   "situacao",               "all"),
    (r"\bpau\s+(?:del[ae]|do|da)\b",      "situacao de",            "all"),
    (r"\bgoz(ar|ou|ando)\b",              "explodir",               "all"),
    (r"\bpunhet(a|eiro)\b",               "situacao",               "all"),
    (r"\bsafadeza\b",                     "malicia",                "all"),
    (r"\bviadagem\b",                     "situacao",               "all"),
    (r"\bviado\b",                        "pessoa",                 "all"),

    # --- Palavroes PT (context=all) ---
    (r"\bporra\b",                        "absurdo",                "all"),
    (r"\bpoha\b",                         "incrivel",               "all"),
    (r"\bmerda\b",                        "problema",               "all"),
    (r"\bca+ralho\b",                     "inacreditavel",          "all"),
    (r"\bkrl\b",                          "inacreditavel",          "all"),
    (r"\bputa\s+(?:que|merda|vida)\b",    "situacao absurda",       "all"),
    (r"\bfilho\s+da\s+puta\b",            "individuo",              "all"),
    (r"\bfilho\s+da\s+put[a@]\b",         "individuo",              "all"),
    (r"\bf[d@]p\b",                       "individuo",              "all"),
    (r"\bbosta\b",                        "absurdo",                "all"),
    (r"\bdesgraça\b",                     "situacao critica",       "all"),
    (r"\bdesgraca\b",                     "situacao critica",       "all"),
    (r"\bvtnc\b",                         "inacreditavel",          "all"),
    (r"\bvsf\b",                          "inacreditavel",          "all"),
    (r"\bqpd\b",                          "situacao",               "all"),

    # --- Termos sexuais EN (context=all) ---
    (r"\bfuck(?:ing|ed|er|s)?\b",         "f*ck",                   "all"),
    (r"\bsh[i1]t\b",                      "sh*t",                   "all"),
    (r"\basshole\b",                      "@sshole",                "all"),
    (r"\bb[i1]tch\b",                     "b*tch",                  "all"),
    (r"\bporn(?:ography|o)?\b",           "adult content",          "all"),
    (r"\bsex tape\b",                     "+18 content",            "all"),
    (r"\borgy\b",                         "intimate situation",     "all"),
    (r"\bprostitut(?:e|ion)\b",           "intimate scandal",       "all"),
    (r"\bcock\b",                         "situation",              "all"),
    (r"\bpussy\b",                        "situation",              "all"),
    (r"\bcum(?:ming|med|s)?\b",           "moment",                 "all"),
    (r"\bdick\b",                         "situation",              "all"),

    # --- Violencia/drogas PT (context=all) ---
    (r"\bassassinato\b",                  "caso chocante",          "all"),
    (r"\bestupro\b",                      "crime grave",            "all"),
    (r"\bsuicid[oi]o?\b",                 "historia pesada",        "all"),
    (r"\bmassacre\b",                     "tragedia",               "all"),
    (r"\bfusilamento\b",                  "caso extremo",           "all"),
    (r"\bcocaina\b",                      "substancias",            "all"),
    (r"\bheroin[ae]\b",                   "substancias",            "all"),
    (r"\bmaconha\b",                      "substancias",            "all"),
    (r"\bcrak\b",                         "substancias",            "all"),
    (r"\bcrack\b",                        "substancias",            "all"),

    # --- Violencia/drogas EN (context=all) ---
    (r"\bmurder\b",                       "shocking case",          "all"),
    (r"\bsuicide\b",                      "heavy story",            "all"),
    (r"\bmassacre\b",                     "brutal attack",          "all"),
    (r"\bterror(?:ism|ist)?\b",           "extreme act",            "all"),
    (r"\bcocc?aine?\b",                   "substances",             "all"),
    (r"\bheroin\b",                       "substances",             "all"),
    (r"\bmarijuana\b",                    "substances",             "all"),

    # --- Titulo/thumbnail adicionais (context=title — mais restritivo) ---
    (r"\bputa\b",                         "incrivel",               "title"),
    (r"\bsexo\b",                         "envolvimento",           "title"),
    (r"\bnua?\b",                         "revelador",              "title"),
    (r"\bnu(?:zinha|zao)?\b",             "revelador",              "title"),
    (r"\bpel[ao]do?\b",                   "sem filtro",             "title"),
    (r"\bpelad[ao]\b",                    "sem filtro",             "title"),
    (r"\bsafad[ao]\b",                    "malicioso",              "title"),
    (r"\bnaked\b",                        "uncensored",             "title"),
    (r"\bexplicit\b",                     "unfiltered",             "title"),
]
# fmt: on

_TITLE_FIELDS = {"suggested_title", "thumbnail_text", "title_suggestion", "title"}
_ALL_FIELDS = _TITLE_FIELDS | {
    "hook_sentence",
    "hook",
    "suggested_description",
    "suggested_first_comment",
    "reason",
}

_COMPILED: list[tuple[re.Pattern, str, str]] = [
    (re.compile(pattern, re.IGNORECASE | re.UNICODE), replacement, ctx)
    for pattern, replacement, ctx in _SUBSTITUTIONS
]


def _sanitize_field(field_name: str, value: str, clip_ref: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return value
    is_title = field_name in _TITLE_FIELDS
    result = value
    for pattern, replacement, ctx in _COMPILED:
        if ctx == "title" and not is_title:
            continue
        new = pattern.sub(replacement, result)
        if new != result:
            logger.warning(
                "[SANITIZER] Campo '%s' do clip %s: substituiu '%s' → '%s' | texto: %r → %r",
                field_name,
                clip_ref,
                pattern.pattern,
                replacement,
                result[:120],
                new[:120],
            )
            result = new
    return result


def sanitize_clip(clip: dict, clip_ref: str = "?") -> dict:
    """Sanitiza um clip dict in-place (modifica e retorna o mesmo dict).

    clip_ref: string de identificacao para o log (ex: 'short#3').
    """
    if not isinstance(clip, dict):
        return clip
    for field in _ALL_FIELDS:
        if field in clip:
            clip[field] = _sanitize_field(field, clip.get(field, ""), clip_ref)
    return clip


def sanitize_payload(payload: dict) -> dict:
    """Sanitiza todos os clips em candidate_shorts, ranked_shorts e final_long_cuts."""
    if not isinstance(payload, dict):
        return payload
    for list_key in ("candidate_shorts", "ranked_shorts", "final_long_cuts"):
        clips = payload.get(list_key)
        if not isinstance(clips, list):
            continue
        for i, clip in enumerate(clips):
            sanitize_clip(clip, clip_ref=f"{list_key}[{i}]")
    return payload
