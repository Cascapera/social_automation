"""
Cliente HTTP para analytics Upload Post (api.upload-post.com).

Autenticação: Authorization: Apikey <UPLOAD_POST_API_KEY> (igual ao publisher em upload_post.py).

Documentação: https://docs.upload-post.com/api/get-analytics
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

UPLOAD_POST_API_ROOT = "https://api.upload-post.com/api"


def get_upload_post_api_key() -> str:
    return (os.getenv("UPLOAD_POST_API_KEY") or getattr(settings, "UPLOAD_POST_API_KEY", "") or "").strip()


def _headers() -> dict[str, str]:
    key = get_upload_post_api_key()
    if not key:
        return {}
    return {"Authorization": f"Apikey {key}"}


def fetch_profile_platforms_analytics(profile_username: str, platforms: str = "youtube") -> tuple[dict[str, Any] | None, str | None]:
    """
    GET /api/analytics/<profile_username>?platforms=<platforms>

    Retorna o JSON completo (ex.: chave "youtube" com followers, views, reach_timeseries, ...).
    Em erro HTTP ou rede: (None, mensagem).
    """
    key = get_upload_post_api_key()
    if not key:
        return None, "UPLOAD_POST_API_KEY não configurada"

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

    if resp.status_code == 404:
        return None, "Perfil não encontrado no Upload Post (conecte o canal brand_<id> no painel)"
    if resp.status_code == 401:
        return None, "Upload Post: API key inválida ou expirada"
    if resp.status_code >= 400:
        msg = (resp.text or "")[:500] or f"HTTP {resp.status_code}"
        logger.warning("[UploadPostAnalytics] profile=%s status=%s: %s", profile_username, resp.status_code, msg)
        return None, msg

    try:
        data = resp.json()
    except Exception:
        return None, "Resposta inválida (JSON)"

    return data, None


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

    Usado para métricas por período (views, likes, video_count, etc.) e opcionalmente per_day.
    """
    key = get_upload_post_api_key()
    if not key:
        return None, "UPLOAD_POST_API_KEY não configurada"

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

    try:
        resp = requests.get(url, params=params, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        logger.warning("[UploadPostAnalytics] rede total-impressions profile=%s: %s", profile_username, e)
        return None, f"Erro de rede: {e}"

    if resp.status_code == 404:
        return None, "Perfil não encontrado no Upload Post"
    if resp.status_code == 401:
        return None, "Upload Post: API key inválida ou expirada"
    if resp.status_code >= 400:
        msg = (resp.text or "")[:500] or f"HTTP {resp.status_code}"
        return None, msg

    try:
        data = resp.json()
    except Exception:
        return None, "Resposta inválida (JSON)"

    if not data.get("success", True) and "total_impressions" not in data and "metrics" not in data:
        return None, data.get("error") or "Resposta sem dados"

    return data, None


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

    if resp.status_code == 404:
        return None, "Post não encontrado no Upload Post"
    if resp.status_code >= 400:
        return None, (resp.text or "")[:300] or f"HTTP {resp.status_code}"

    try:
        return resp.json(), None
    except Exception:
        return None, "Resposta inválida (JSON)"
