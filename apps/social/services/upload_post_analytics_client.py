"""
Cliente HTTP para analytics Upload Post (api.upload-post.com).

Autenticação: Authorization: Apikey <UPLOAD_POST_API_KEY> (igual ao publisher em upload_post.py).

Documentação: https://docs.upload-post.com/api/get-analytics

Inclui: throttle entre requisições, mensagens de erro legíveis, tratamento de HTTP 429,
corpos JSON de erro mesmo com status 200, e fallback do endpoint total-impressions
sem parâmetro ``metrics`` quando a API falha ao processar métricas compostas.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

UPLOAD_POST_API_ROOT = "https://api.upload-post.com/api"

_UPLOAD_POST_MIN_INTERVAL = float(os.getenv("UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC", "0.35"))
_last_request_mono: float = 0.0


def _throttle_upload_post() -> None:
    """Garante intervalo mínimo entre requisições de analytics ao Upload Post."""
    global _last_request_mono
    now = time.monotonic()
    wait = _UPLOAD_POST_MIN_INTERVAL - (now - _last_request_mono)
    if wait > 0:
        time.sleep(wait)
    _last_request_mono = time.monotonic()


def get_upload_post_api_key() -> str:
    return (os.getenv("UPLOAD_POST_API_KEY") or getattr(settings, "UPLOAD_POST_API_KEY", "") or "").strip()


def _headers() -> dict[str, str]:
    key = get_upload_post_api_key()
    if not key:
        return {}
    return {"Authorization": f"Apikey {key}"}


def _parse_json_dict(text: str) -> dict[str, Any] | None:
    try:
        j = json.loads(text)
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def _format_http_error_body(resp: requests.Response) -> str:
    """Extrai mensagem curta de JSON de erro; evita exibir o JSON inteiro na UI."""
    raw = (resp.text or "").strip()
    if not raw:
        return f"HTTP {resp.status_code}"
    d = _parse_json_dict(raw) if raw.startswith("{") else None
    if d:
        inner = d.get("error")
        if inner is not None:
            return str(inner)[:480]
        msg = d.get("message")
        if msg:
            return str(msg)[:480]
    return raw[:480]


def _application_level_error(data: Any) -> str | None:
    """
    A API pode devolver HTTP 200 com JSON de falha (ex.: erro interno no servidor deles).
    Não confundir com payload válido que também contenha chave ``error`` opcional.
    """
    if data is None:
        return "Resposta JSON nula"
    if not isinstance(data, dict):
        return None
    if data.get("metrics") is not None:
        return None
    if data.get("total_impressions") is not None:
        return None
    err = data.get("error")
    if err is None:
        return None
    msg = data.get("message")
    if msg == "Something went wrong":
        return str(err)[:480]
    if data.get("success") is False:
        return str(err)[:480]
    if data.get("data") is None and msg and "wrong" in str(msg).lower():
        return str(err)[:480]
    return None


def fetch_profile_platforms_analytics(profile_username: str, platforms: str = "youtube") -> tuple[dict[str, Any] | None, str | None]:
    """
    GET /api/analytics/<profile_username>?platforms=<platforms>

    Retorna o JSON completo (ex.: chave "youtube" com followers, views, reach_timeseries, ...).
    Em erro HTTP ou rede: (None, mensagem).
    """
    key = get_upload_post_api_key()
    if not key:
        return None, "UPLOAD_POST_API_KEY não configurada"

    _throttle_upload_post()
    url = f"{UPLOAD_POST_API_ROOT}/analytics/{quote(profile_username, safe='')}"
    try:
        resp = requests.get(
            url,
            params={"platforms": platforms},
            headers=_headers(),
            timeout=30,
        )
    except requests.RequestException as e:
        logger.warning("[UploadPostAnalytics] rede profile=%s: %s", profile_username, e)
        return None, f"Erro de rede: {e}"

    if resp.status_code == 429:
        ra = resp.headers.get("Retry-After", "?")
        return None, f"Rate limit (429). Aguarde {ra}s ou aumente UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC."

    if resp.status_code == 404:
        return None, "Perfil não encontrado no Upload Post (conecte o canal brand_<id> no painel)"
    if resp.status_code == 401:
        return None, "Upload Post: API key inválida ou expirada"
    if resp.status_code >= 400:
        msg = _format_http_error_body(resp)
        logger.warning("[UploadPostAnalytics] profile=%s status=%s: %s", profile_username, resp.status_code, msg)
        return None, msg

    try:
        data = resp.json()
    except Exception:
        return None, "Resposta inválida (JSON)"

    app_err = _application_level_error(data)
    if app_err:
        logger.warning("[UploadPostAnalytics] profile=%s app_error: %s", profile_username, app_err)
        return None, app_err

    return data, None


def _fetch_total_impressions_request(
    profile_username: str,
    *,
    period: str | None,
    start_date: str | None,
    end_date: str | None,
    platform: str,
    metrics: str | None,
    breakdown: bool,
) -> requests.Response:
    _throttle_upload_post()
    url = f"{UPLOAD_POST_API_ROOT}/uploadposts/total-impressions/{quote(profile_username, safe='')}"
    params: dict[str, Any] = {
        "platform": platform,
        "breakdown": "true" if breakdown else "false",
    }
    if period:
        params["period"] = period
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if metrics:
        params["metrics"] = metrics
    return requests.get(url, params=params, headers=_headers(), timeout=30)


def fetch_total_impressions(
    profile_username: str,
    *,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    platform: str = "youtube",
    metrics: str | None = None,
    breakdown: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    GET /api/uploadposts/total-impressions/<profile_username>

    Se a chamada com ``metrics`` composto falhar (erro conhecido da API), tenta de novo
    **sem** ``metrics``, usando ``total_impressions`` + ``per_day`` no nível raiz.
    """
    key = get_upload_post_api_key()
    if not key:
        return None, "UPLOAD_POST_API_KEY não configurada"

    def _parse_response(resp: requests.Response) -> tuple[dict[str, Any] | None, str | None]:
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After", "?")
            return None, f"Rate limit (429). Aguarde {ra}s ou aumente UPLOAD_POST_ANALYTICS_MIN_INTERVAL_SEC."
        if resp.status_code == 404:
            return None, "Perfil não encontrado no Upload Post"
        if resp.status_code == 401:
            return None, "Upload Post: API key inválida ou expirada"
        if resp.status_code >= 400:
            return None, _format_http_error_body(resp)
        try:
            data = resp.json()
        except Exception:
            return None, "Resposta inválida (JSON)"
        if data is None:
            return None, "Resposta JSON nula"
        app_err = _application_level_error(data)
        if app_err:
            return None, app_err
        if isinstance(data, dict) and not data.get("success", True):
            if "metrics" not in data and "total_impressions" not in data:
                return None, str(data.get("error") or "Resposta sem dados")
        return data, None

    try:
        resp = _fetch_total_impressions_request(
            profile_username,
            period=period,
            start_date=start_date,
            end_date=end_date,
            platform=platform,
            metrics=metrics,
            breakdown=breakdown,
        )
    except requests.RequestException as e:
        logger.warning("[UploadPostAnalytics] rede total-impressions profile=%s: %s", profile_username, e)
        return None, f"Erro de rede: {e}"

    data, err = _parse_response(resp)

    if err is None and isinstance(data, dict):
        return data, None

    # Fallback: alguns perfis falham no servidor ao pedir várias métricas de uma vez
    if metrics:
        logger.info(
            "[UploadPostAnalytics] total-impressions retry sem metrics profile=%s erro=%s",
            profile_username,
            err,
        )
        try:
            resp2 = _fetch_total_impressions_request(
                profile_username,
                period=period,
                start_date=start_date,
                end_date=end_date,
                platform=platform,
                metrics=None,
                breakdown=breakdown,
            )
        except requests.RequestException as e:
            return None, f"Erro de rede (retry): {e}"
        data2, err2 = _parse_response(resp2)
        if err2 is None and isinstance(data2, dict):
            data2["_upload_post_fallback_no_metrics"] = True
            return data2, None
        return None, err2 or err

    return None, err


def fetch_post_analytics(request_id: str, platform: str = "youtube") -> tuple[dict[str, Any] | None, str | None]:
    """
    GET /api/uploadposts/post-analytics/<request_id>?platform=youtube
    """
    key = get_upload_post_api_key()
    if not key:
        return None, "UPLOAD_POST_API_KEY não configurada"

    rid = (request_id or "").strip()
    if not rid:
        return None, "request_id vazio"

    _throttle_upload_post()
    url = f"{UPLOAD_POST_API_ROOT}/uploadposts/post-analytics/{quote(rid, safe='')}"
    try:
        resp = requests.get(
            url,
            params={"platform": platform},
            headers=_headers(),
            timeout=25,
        )
    except requests.RequestException as e:
        return None, f"Erro de rede: {e}"

    if resp.status_code == 429:
        ra = resp.headers.get("Retry-After", "?")
        return None, f"Rate limit (429). Aguarde {ra}s"

    if resp.status_code == 404:
        return None, "Post não encontrado no Upload Post"
    if resp.status_code >= 400:
        return None, _format_http_error_body(resp)

    try:
        data = resp.json()
    except Exception:
        return None, "Resposta inválida (JSON)"
    if data is None:
        return None, "Resposta JSON nula"
    app_err = _application_level_error(data)
    if app_err:
        return None, app_err
    return data, None
