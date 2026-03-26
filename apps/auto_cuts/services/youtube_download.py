"""Download de videos do YouTube via yt-dlp."""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _yt_dlp_youtube_extractor_options(*, has_cookies: bool) -> dict:
    """
    player_client: com cookies, clientes "android/ios" são ignorados pelo yt-dlp
    ("Skipping client … since it does not support cookies") — usar só clients web.
    Sem cookies: tenta android/web/ios.
    EJS: precisa de runtime JS (Deno no Dockerfile) + pip install 'yt-dlp[default]' (yt-dlp-ejs).
    """
    raw = (os.getenv("YTDLP_YOUTUBE_PLAYER_CLIENTS") or "").strip()
    if raw:
        clients = [x.strip() for x in raw.split(",") if x.strip()]
    elif has_cookies:
        clients = ["web", "mweb", "tv_embedded"]
    else:
        clients = ["android", "web", "ios"]
    if not clients:
        return {}
    return {
        "extractor_args": {
            "youtube": {
                "player_client": clients,
            },
        },
    }


def _yt_dlp_js_runtime_options() -> dict:
    """
    Por omissão o yt-dlp já usa Deno (js_runtimes=['deno']). Só sobrescreve com YTDLP_JS_RUNTIMES
    (ex.: 'node', 'deno,node', 'node:/usr/bin/node'). Valores separados por vírgula.
    """
    raw = (os.getenv("YTDLP_JS_RUNTIMES") or "").strip()
    if not raw:
        return {}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return {"js_runtimes": parts} if parts else {}


def _yt_dlp_cookie_options() -> dict:
    """
    YouTube frequentemente exige sessão autenticada (anti-bot). Configure no .env:

    - YTDLP_COOKIES_FILE=/caminho/para/youtube_cookies.txt (Netscape; recomendado em Docker)
    - YTDLP_COOKIES_FROM_BROWSER=chrome  ou  firefox:perfil  (lê cookies do PC onde roda o worker)

    Ver: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
    """
    opts: dict = {}
    cookiefile = (os.getenv("YTDLP_COOKIES_FILE") or "").strip()
    if cookiefile:
        p = Path(cookiefile).expanduser()
        if p.is_file():
            opts["cookiefile"] = str(p.resolve())
            logger.info("[YTDLP] Usando cookies (arquivo YTDLP_COOKIES_FILE)")
            return opts
        logger.warning(
            "[YTDLP] YTDLP_COOKIES_FILE definido mas arquivo inexistente: %s",
            p,
        )

    browser = (os.getenv("YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if not browser:
        return opts

    parts = browser.split(":", 1)
    name = (parts[0] or "").strip().lower()
    if not name:
        return opts
    profile = parts[1].strip() if len(parts) > 1 else None
    if profile:
        opts["cookiesfrombrowser"] = (name, profile)
    else:
        opts["cookiesfrombrowser"] = (name,)
    logger.info("[YTDLP] Usando cookies do navegador (YTDLP_COOKIES_FROM_BROWSER=%s)", browser)
    return opts


def _youtube_format_candidates() -> list[str]:
    """
    Lista de strings de formato para yt-dlp, da mais desejada à fallback.

    YTDLP_MIN_VIDEO_HEIGHT (ex.: 720, 1080): tenta primeiro vídeo com altura >= N px.
    Use 0 para desativar o filtro e manter só o comportamento antigo (melhor disponível).
    """
    raw = (os.getenv("YTDLP_MIN_VIDEO_HEIGHT") or "").strip()
    try:
        min_h = int(raw) if raw else 720
    except ValueError:
        min_h = 720
    min_h = max(0, min_h)

    # Fallbacks sem filtro de altura (se o vídeo não tiver 720p/1080p, ainda baixa o melhor possível).
    base = [
        "bestvideo+bestaudio/best",
        "bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
        "best[ext=mp4]/best",
        "best",
    ]
    if min_h <= 0:
        return base

    logger.info("[YTDLP] Altura mínima desejada: %s px (defina YTDLP_MIN_VIDEO_HEIGHT=0 para desativar)", min_h)
    prefixed = [
        f"bestvideo[height>={min_h}]+bestaudio/best",
        f"bestvideo[height>={min_h}][ext=mp4]+bestaudio/best",
    ]
    seen = set()
    out: list[str] = []
    for f in prefixed + base:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def download_youtube(
    url: str,
    output_path: Path,
) -> Path:
    """
    Baixa vídeo do YouTube (ou suportados pelo yt-dlp) para o caminho indicado.
    Retorna o Path do arquivo baixado.

    Usa a melhor combinação de formatos que o YouTube oferece (sem forçar idioma de áudio).
    Com YTDLP_MIN_VIDEO_HEIGHT (padrão 720), tenta primeiro faixas com altura mínima.
    """
    import yt_dlp

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Template: base.%(ext)s para forcar nome e obter .mp4
    out_template = str(output_path.with_suffix("")) + ".%(ext)s"

    format_candidates = _youtube_format_candidates()
    last_exc: Exception | None = None

    cookie_opts = _yt_dlp_cookie_options()
    has_cookies = bool(cookie_opts.get("cookiefile") or cookie_opts.get("cookiesfrombrowser"))
    extractor_opts = _yt_dlp_youtube_extractor_options(has_cookies=has_cookies)
    js_opts = _yt_dlp_js_runtime_options()

    for idx, fmt in enumerate(format_candidates, start=1):
        opts = {
            "format": fmt,
            "outtmpl": out_template,
            "merge_output_format": "mp4",
            "quiet": False,
            "no_warnings": False,
            "retries": 6,
            "fragment_retries": 6,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "socket_timeout": 25,
            # Evita rota IPv6 instavel em alguns hosts/CDNs no Docker/WSL.
            "force_ipv4": True,
            **js_opts,
            **extractor_opts,
            **cookie_opts,
        }
        try:
            logger.info("[YTDLP] Tentativa %s com formato: %s", idx, fmt)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise ValueError("Nao foi possivel obter informacoes do video")
                filename = ydl.prepare_filename(info)
                result = Path(filename)
                if not result.exists():
                    raise FileNotFoundError(f"Arquivo nao foi gerado: {result}")
                return result
        except Exception as exc:
            last_exc = exc
            logger.warning("[YTDLP] Falha na tentativa %s (%s): %s", idx, fmt, exc)
            # pequeno backoff entre tentativas para reduzir erro de rede temporario
            time.sleep(min(4 * idx, 12))

    msg = f"Failed to download YouTube video after fallbacks: {last_exc}"
    err_s = str(last_exc).lower()
    if "sign in" in err_s or "not a bot" in err_s or "cookies" in err_s:
        msg += (
            " Configure YTDLP_COOKIES_FILE (arquivo Netscape) ou YTDLP_COOKIES_FROM_BROWSER no .env — "
            "veja README e https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
        )
    if (
        "challenge" in err_s
        or "only images" in err_s
        or "ejs" in err_s
        or "javascript runtime" in err_s
    ):
        msg += (
            " Desafio do YouTube (EJS): use `pip install -U \"yt-dlp[default]\"` (pacote yt-dlp-ejs), "
            "imagem Docker com Deno (`docker compose build --no-cache`) e veja "
            "https://github.com/yt-dlp/yt-dlp/wiki/EJS"
        )
    raise RuntimeError(msg)
