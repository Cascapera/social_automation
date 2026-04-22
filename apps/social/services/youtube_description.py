"""Monta descrição YouTube igual à usada na postagem (API e Upload Post)."""
import logging
import random
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")

_MAX_HASHTAGS = 5
_MIN_HASHTAGS_REQUIRED = 3

_RELATED_LINKS_POOL_SIZE = 10
_RELATED_LINKS_CHOICE_COUNT = 2


def _tag_to_hashtag(tag: str) -> str:
    """Converte uma tag em #hashtag removendo acentos, espacos e simbolos."""
    s = str(tag or "").strip().lower()
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9]+", "", ascii_only)
    if not cleaned:
        return ""
    return f"#{cleaned}"


def _build_hashtags_block(corte) -> str:
    """Monta linha de hashtags a partir de suggestion.raw_data['tags'].

    Retorna "" se nao houver tags suficientes.
    """
    suggestion = getattr(corte, "suggestion", None) if corte else None
    raw = getattr(suggestion, "raw_data", None) or {}
    tags = raw.get("tags")
    if not isinstance(tags, list) or len(tags) < _MIN_HASHTAGS_REQUIRED:
        return ""
    hashtags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        h = _tag_to_hashtag(tag)
        if not h or h in seen:
            continue
        seen.add(h)
        hashtags.append(h)
        if len(hashtags) >= _MAX_HASHTAGS:
            break
    if len(hashtags) < _MIN_HASHTAGS_REQUIRED:
        return ""
    return " ".join(hashtags)


def _is_long_cut(corte) -> bool:
    suggestion = getattr(corte, "suggestion", None) if corte else None
    cut_type = (getattr(suggestion, "cut_type", "") or "").strip().lower()
    return cut_type == "long"


def _normalize_chapter_timestamp(value: str) -> str:
    s = str(value or "").strip()
    if not _TIMESTAMP_RE.match(s):
        return ""
    parts = s.split(":")
    if len(parts) == 2:
        # MM:SS -> garante zero-pad
        mm, ss = parts
        try:
            return f"{int(mm):02d}:{int(ss):02d}"
        except ValueError:
            return ""
    # HH:MM:SS
    hh, mm, ss = parts
    try:
        return f"{int(hh):d}:{int(mm):02d}:{int(ss):02d}"
    except ValueError:
        return ""


def _build_chapters_block(corte, is_en: bool) -> str:
    """Monta bloco de capítulos se o corte for longo e houver dados válidos.

    Retorna string vazia se não aplicável. Regras do YouTube para capítulos:
    - primeiro timestamp deve ser 00:00
    - mínimo 3 capítulos
    - timestamps em ordem crescente
    """
    if not _is_long_cut(corte):
        return ""
    suggestion = getattr(corte, "suggestion", None)
    raw = getattr(suggestion, "raw_data", None) or {}
    chapters = raw.get("chapters")
    if not isinstance(chapters, list) or len(chapters) < 3:
        return ""
    cleaned: list[tuple[str, str]] = []
    for item in chapters:
        if not isinstance(item, dict):
            continue
        ts = _normalize_chapter_timestamp(item.get("timestamp"))
        title = str(item.get("title") or "").strip()
        if not ts or not title:
            continue
        cleaned.append((ts, title[:100]))
        if len(cleaned) >= 8:
            break
    if len(cleaned) < 3:
        return ""
    if cleaned[0][0] not in ("00:00", "0:00"):
        return ""
    cleaned[0] = ("00:00", cleaned[0][1])
    header = "📍 Chapters:" if is_en else "📍 Capítulos:"
    lines = [header] + [f"{ts} {title}" for ts, title in cleaned]
    return "\n".join(lines)


def _is_english_corte(corte) -> bool:
    analysis = getattr(corte, "analysis", None) if corte else None
    prompt_version = (getattr(analysis, "prompt_version", "") or "").strip().lower()
    return prompt_version.endswith("_en")


def _build_related_links_block(brand, exclude_corte_id, is_en: bool) -> str:
    """Monta bloco com 1-2 links rotativos de videos ja publicados da mesma brand.

    Pega os ultimos videos DONE com YTB video_id, escolhe aleatoriamente ate
    _RELATED_LINKS_CHOICE_COUNT para montar um bloco "Mais videos".
    Falha silenciosamente se nao houver historico suficiente.
    """
    if not brand or not getattr(brand, "id", None):
        return ""
    try:
        from apps.jobs.models import ScheduledPost
    except Exception:
        return ""
    try:
        qs = (
            ScheduledPost.objects.filter(
                social_account__brand_id=brand.id,
                status="DONE",
                external_ids__has_key="YTB",
            )
            .exclude(auto_cut_corte_id=exclude_corte_id)
            .order_by("-scheduled_at")
            .values_list("external_ids", flat=True)[:_RELATED_LINKS_POOL_SIZE]
        )
        candidates: list[str] = []
        for ext in qs:
            vid = (ext or {}).get("YTB")
            if isinstance(vid, str) and vid.strip():
                candidates.append(vid.strip())
    except Exception as e:
        logger.warning("[YT-DESC] Falha ao buscar videos relacionados (brand=%s): %s", brand.id, e)
        return ""
    if not candidates:
        return ""
    count = min(_RELATED_LINKS_CHOICE_COUNT, len(candidates))
    picks = random.sample(candidates, count)
    header = "▶️ More videos:" if is_en else "▶️ Mais vídeos:"
    lines = [header] + [f"https://youtu.be/{vid}" for vid in picks]
    return "\n".join(lines)


def build_youtube_description(corte, brand=None, title=None, description_override=None):
    """
    Retorna a descrição exata que seria usada ao postar no YouTube.
    Usado no download de mídias para copy-paste manual.

    Args:
        corte: AutoCutCorte (com analysis)
        brand: Brand para youtube_description_extra (fallback: analysis.brand)
        title: Título (opcional, para fluxos sem analysis)
        description_override: Se informado, usa como base (ex: inventory.description)
    """
    is_en = _is_english_corte(corte)
    chapters_block = _build_chapters_block(corte, is_en) if corte else ""
    hashtags_block = _build_hashtags_block(corte) if corte else ""
    related_links_block = ""
    if corte and _is_long_cut(corte):
        exclude_corte_id = getattr(corte, "id", None)
        resolved_brand = brand
        if not resolved_brand:
            analysis_for_brand = getattr(corte, "analysis", None)
            resolved_brand = getattr(analysis_for_brand, "brand", None)
        related_links_block = _build_related_links_block(resolved_brand, exclude_corte_id, is_en)

    if description_override and str(description_override or "").strip():
        base = str(description_override).strip()
        parts = [base]
        if chapters_block:
            parts.append(chapters_block)
        if related_links_block:
            parts.append(related_links_block)
        if hashtags_block:
            parts.append(hashtags_block)
        return "\n\n".join(parts)[:5000]

    analysis = getattr(corte, "analysis", None) if corte else None
    if not analysis:
        base = (title or "").strip()
        parts = [p for p in [base, chapters_block, related_links_block, hashtags_block] if p]
        return "\n\n".join(parts)[:5000] if parts else ""

    video_name = (
        (analysis.name or "").strip()
        or ((analysis.source.title or "").strip() if getattr(analysis, "source", None) else "")
    )
    if not video_name and getattr(analysis, "file", None) and analysis.file.name:
        video_name = Path(analysis.file.name).stem
    if not video_name:
        video_name = "Vídeo original"

    convidados = (analysis.convidados or "").strip() or "-"

    if is_en:
        lines = [
            f"🎙️ Clip from live: {video_name}",
            "",
            f"Guest: {convidados}",
        ]
        full_episode_label = "📺 Full episode:"
    else:
        lines = [
            f"🎙️ Corte da live: {video_name}",
            "",
            f"Convidado: {convidados}",
        ]
        full_episode_label = "📺 Episódio completo:"

    youtube_url = (analysis.youtube_url or "").strip()
    if youtube_url:
        lines.extend(["", full_episode_label, youtube_url])

    auto_part = "\n".join(lines).strip()
    brand_extra = ""
    b = brand or getattr(analysis, "brand", None)
    if b:
        brand_extra = (getattr(b, "youtube_description_extra", None) or "").strip()

    if chapters_block:
        auto_part = f"{auto_part}\n\n{chapters_block}"
    if related_links_block:
        auto_part = f"{auto_part}\n\n{related_links_block}"
    result = auto_part
    if brand_extra:
        result = f"{result}\n\n\n{brand_extra}"
    if hashtags_block:
        result = f"{result}\n\n{hashtags_block}"
    return result[:5000]
