"""Monta descrição YouTube igual à usada na postagem (API e Upload Post)."""
from pathlib import Path


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
    if description_override and str(description_override or "").strip():
        return str(description_override).strip()[:5000]

    analysis = getattr(corte, "analysis", None) if corte else None
    if not analysis:
        return (title or "").strip()[:5000] if title else ""

    video_name = (
        (analysis.name or "").strip()
        or ((analysis.source.title or "").strip() if getattr(analysis, "source", None) else "")
    )
    if not video_name and getattr(analysis, "file", None) and analysis.file.name:
        video_name = Path(analysis.file.name).stem
    if not video_name:
        video_name = "Vídeo original"

    convidados = (analysis.convidados or "").strip() or "-"
    prompt_version = (getattr(analysis, "prompt_version", "") or "").strip().lower()
    is_en = prompt_version.endswith("_en")

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

    if brand_extra:
        return f"{auto_part}\n\n\n{brand_extra}"[:5000]
    return auto_part[:5000]
