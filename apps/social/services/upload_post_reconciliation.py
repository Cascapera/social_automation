"""
Reconciliação de status Upload Post (GET /api/uploadposts/status) antes de fallback nativo.

Documentação OpenAPI: status agregado pending | in_progress | completed + results por plataforma.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import requests
from django.utils import timezone

from apps.social.services.upload_post_analytics_client import (
    UPLOAD_POST_API_ROOT,
    _headers,
    fetch_post_analytics,
    get_upload_post_api_key,
)

logger = logging.getLogger(__name__)

# Chaves em ScheduledPost.external_ids (convênio com o restante do repo)
EXT_UPLOAD_POST_JOB_ID = "upload_post_job_id"
EXT_UPLOAD_POST_REQUEST_ID = "upload_post_request_id"
EXT_UPLOAD_POST_LAST_STATUS = "upload_post_last_status"
EXT_UPLOAD_POST_LAST_CHECKED_AT = "upload_post_last_checked_at"
EXT_UPLOAD_POST_RECONCILIATION_STATE = "upload_post_reconciliation_state"

RECONCILIATION_STATE_PENDING = "pending"


class UploadPostProviderStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ReconcileDecision(StrEnum):
    """Resultado lógico após consultar o provedor."""

    WAIT = "wait"  # ainda processando ou erro transitório na consulta
    CONFIRMED_SUCCESS = "confirmed_success"
    CONFIRMED_FAILURE = "confirmed_failure"  # Upload Post falhou de forma terminal para o escopo analisado
    NO_PROVIDER_ID = "no_provider_id"  # não há request_id/job_id — não dá para consultar


@dataclass(frozen=True)
class ReconcileOutcome:
    decision: ReconcileDecision
    detail: str = ""
    external_ids_patch: dict[str, Any] | None = None
    youtube_video_id: str | None = None
    next_delay_seconds: int = 120
    raw_status_payload: dict[str, Any] | None = None


_YOUTUBE_VIDEO_ID_RE = re.compile(r"([a-zA-Z0-9_-]{11})(?=[\s?#&\"']|$)")
_URL_LAST_SEGMENT_RE = re.compile(r"/([a-zA-Z0-9_-]{6,})(?:[?\s\"']|$)")


def _parse_generic_id_from_message(message: str) -> str | None:
    """Heurística para IDs em mensagens/URLs de outras plataformas."""
    if not message:
        return None
    m = _URL_LAST_SEGMENT_RE.search(message)
    if m:
        return m.group(1)
    return None


def _parse_youtube_video_id_from_message(message: str) -> str | None:
    if not message:
        return None
    m = _YOUTUBE_VIDEO_ID_RE.search(message)
    if m:
        return m.group(1)
    return None


def fetch_upload_post_status(
    *,
    request_id: str | None = None,
    job_id: str | None = None,
    timeout: int = 30,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    GET /api/uploadposts/status?request_id=... ou job_id=...
    """
    rid = (request_id or "").strip()
    jid = (job_id or "").strip()
    if not rid and not jid:
        return None, "request_id e job_id vazios"

    key = get_upload_post_api_key()
    if not key:
        return None, "UPLOAD_POST_API_KEY não configurada"

    params: dict[str, str] = {}
    if rid:
        params["request_id"] = rid
    if jid:
        params["job_id"] = jid

    url = f"{UPLOAD_POST_API_ROOT}/uploadposts/status"
    try:
        resp = requests.get(url, params=params, headers=_headers(), timeout=timeout)
    except requests.RequestException as e:
        logger.warning("[UploadPostReconcile] rede status request_id=%s job_id=%s: %s", rid, jid, e)
        return None, f"Erro de rede: {e}"

    if resp.status_code == 429:
        ra = resp.headers.get("Retry-After", "?")
        return None, f"Rate limit (429), Retry-After={ra}"

    if resp.status_code >= 400:
        body = (resp.text or "")[:500]
        logger.warning(
            "[UploadPostReconcile] HTTP %s status request_id=%s job_id=%s body=%s",
            resp.status_code,
            rid,
            jid,
            body,
        )
        return None, f"HTTP {resp.status_code}: {body}"

    try:
        data = resp.json()
    except Exception:
        return None, "Resposta inválida (JSON)"

    if not isinstance(data, dict):
        return None, "Resposta JSON inesperada"
    return data, None


def _youtube_result_row(status_payload: dict[str, Any]) -> dict[str, Any] | None:
    results = status_payload.get("results")
    if not isinstance(results, list):
        return None
    for row in results:
        if not isinstance(row, dict):
            continue
        plat = str(row.get("platform") or "").lower()
        if plat == "youtube":
            return row
    return None


def reconcile_upload_post_status(
    *,
    external_ids: dict[str, Any],
    needs_youtube: bool,
) -> ReconcileOutcome:
    """
    Consulta Upload Post e decide se o YouTube (quando ``needs_youtube``) já concluiu, falhou ou segue pendente.

    Sem request_id/job_id: NO_PROVIDER_ID (comportamento conservador — não assume falha).
    """
    ext = external_ids or {}
    rid = str(ext.get(EXT_UPLOAD_POST_REQUEST_ID) or ext.get("upload_post_request_id") or "").strip()
    jid = str(ext.get(EXT_UPLOAD_POST_JOB_ID) or "").strip()

    now_iso = timezone.now().isoformat()
    base_patch: dict[str, Any] = {
        EXT_UPLOAD_POST_LAST_CHECKED_AT: now_iso,
    }

    if not rid and not jid:
        return ReconcileOutcome(
            decision=ReconcileDecision.NO_PROVIDER_ID,
            detail="Sem upload_post_request_id nem upload_post_job_id; não é possível confirmar no provedor",
            external_ids_patch={
                **base_patch,
                EXT_UPLOAD_POST_LAST_STATUS: "no_provider_id",
            },
            next_delay_seconds=300,
        )

    data, err = fetch_upload_post_status(request_id=rid or None, job_id=jid or None)
    if err:
        return ReconcileOutcome(
            decision=ReconcileDecision.WAIT,
            detail=err,
            external_ids_patch={
                **base_patch,
                EXT_UPLOAD_POST_LAST_STATUS: f"status_query_error:{err[:200]}",
            },
            next_delay_seconds=120,
        )

    raw_status = str(data.get("status") or "").strip().lower()
    # Algumas respostas usam "queued" como sinónimo de ainda não concluído.
    if raw_status == "queued":
        raw_status = "pending"
    base_patch[EXT_UPLOAD_POST_LAST_STATUS] = raw_status or "unknown"

    try:
        normalized = UploadPostProviderStatus(raw_status) if raw_status else None
    except ValueError:
        normalized = None

    if normalized in (UploadPostProviderStatus.PENDING, UploadPostProviderStatus.IN_PROGRESS):
        return ReconcileOutcome(
            decision=ReconcileDecision.WAIT,
            detail=f"Provedor status={raw_status}",
            external_ids_patch=base_patch,
            next_delay_seconds=90,
            raw_status_payload=data,
        )

    if normalized == UploadPostProviderStatus.COMPLETED:
        yt_row = _youtube_result_row(data)
        if needs_youtube:
            if not yt_row:
                # completed agregado sem linha youtube — tenta post-analytics
                if rid:
                    pa, pa_err = fetch_post_analytics(rid, platform="youtube")
                    if pa_err or not pa:
                        return ReconcileOutcome(
                            decision=ReconcileDecision.WAIT,
                            detail=f"completed sem linha youtube; post-analytics: {pa_err or 'empty'}",
                            external_ids_patch=base_patch,
                            next_delay_seconds=120,
                            raw_status_payload=data,
                        )
                    plat = (pa.get("platforms") or {}).get("youtube") or {}
                    post_url = str(plat.get("post_url") or "") if isinstance(plat, dict) else ""
                    vid = _parse_youtube_video_id_from_message(post_url)
                    if not vid and isinstance(plat, dict):
                        pm = plat.get("post_metrics") or {}
                        if isinstance(pm, dict):
                            vid = _parse_youtube_video_id_from_message(str(pm.get("video_id") or ""))
                    if vid:
                        return ReconcileOutcome(
                            decision=ReconcileDecision.CONFIRMED_SUCCESS,
                            detail="completed (via post-analytics)",
                            youtube_video_id=vid,
                            external_ids_patch={
                                **base_patch,
                                EXT_UPLOAD_POST_LAST_STATUS: "completed_youtube_confirmed",
                                EXT_UPLOAD_POST_RECONCILIATION_STATE: "",
                            },
                            raw_status_payload=data,
                        )
                return ReconcileOutcome(
                    decision=ReconcileDecision.WAIT,
                    detail="completed sem detalhe YouTube ainda",
                    external_ids_patch=base_patch,
                    next_delay_seconds=120,
                    raw_status_payload=data,
                )

            success = bool(yt_row.get("success"))
            message = str(yt_row.get("message") or "")
            vid = _parse_youtube_video_id_from_message(message)
            if not vid and rid:
                pa, _ = fetch_post_analytics(rid, platform="youtube")
                if isinstance(pa, dict):
                    plat = (pa.get("platforms") or {}).get("youtube") or {}
                    post_url = str(plat.get("post_url") or "") if isinstance(plat, dict) else ""
                    vid = _parse_youtube_video_id_from_message(post_url)

            if success and vid:
                return ReconcileOutcome(
                    decision=ReconcileDecision.CONFIRMED_SUCCESS,
                    detail="completed youtube success",
                    youtube_video_id=vid,
                    external_ids_patch={
                        **base_patch,
                        EXT_UPLOAD_POST_LAST_STATUS: "completed",
                        EXT_UPLOAD_POST_RECONCILIATION_STATE: "",
                    },
                    raw_status_payload=data,
                )
            if success and not vid:
                return ReconcileOutcome(
                    decision=ReconcileDecision.WAIT,
                    detail="youtube success sem video_id parseável",
                    external_ids_patch=base_patch,
                    next_delay_seconds=90,
                    raw_status_payload=data,
                )
            # success == False
            return ReconcileOutcome(
                decision=ReconcileDecision.CONFIRMED_FAILURE,
                detail=f"youtube falhou no Upload Post: {message[:300]}",
                external_ids_patch={
                    **base_patch,
                    EXT_UPLOAD_POST_LAST_STATUS: "failed_youtube",
                    EXT_UPLOAD_POST_RECONCILIATION_STATE: "",
                },
                raw_status_payload=data,
            )

        # Não precisa de YouTube neste reconcile (ex.: só redes)
        return ReconcileOutcome(
            decision=ReconcileDecision.CONFIRMED_SUCCESS,
            detail="completed (sem verificação YouTube)",
            external_ids_patch={
                **base_patch,
                EXT_UPLOAD_POST_RECONCILIATION_STATE: "",
            },
            raw_status_payload=data,
        )

    return ReconcileOutcome(
        decision=ReconcileDecision.WAIT,
        detail=f"status não reconhecido: {raw_status}",
        external_ids_patch=base_patch,
        next_delay_seconds=120,
        raw_status_payload=data,
    )


def logical_platform_for_upload_post(upload_post_platform: str, post_platforms: list[str]) -> str:
    u = str(upload_post_platform).strip().upper()
    if u == "YOUTUBE":
        return "YT" if ("YT" in post_platforms and "YTB" not in post_platforms) else "YTB"
    return {"TIKTOK": "TT", "INSTAGRAM": "IG", "X": "X"}[u]


def merge_completed_upload_status_into_external_ids(
    data: dict[str, Any],
    *,
    post_platforms: list[str],
    upload_post_platforms: list[str],
    external_ids: dict[str, Any],
) -> tuple[str | None, bool]:
    """
    Preenche external_ids a partir de ``results`` quando status completed.
    Retorna (youtube_video_id ou None, all_expected_filled).
    """
    results = data.get("results")
    if not isinstance(results, list):
        return None, False

    rows_by_plat: dict[str, dict[str, Any]] = {}
    for row in results:
        if not isinstance(row, dict):
            continue
        p = str(row.get("platform") or "").lower()
        if p:
            rows_by_plat[p] = row

    youtube_vid: str | None = None
    for up in upload_post_platforms:
        u = str(up).strip().upper()
        key = {
            "TIKTOK": "tiktok",
            "X": "x",
            "INSTAGRAM": "instagram",
            "YOUTUBE": "youtube",
        }.get(u)
        if not key:
            continue
        row = rows_by_plat.get(key) or {}
        if not row.get("success"):
            continue
        msg = str(row.get("message") or "")
        logical = logical_platform_for_upload_post(u, post_platforms)
        if u == "YOUTUBE":
            vid = _parse_youtube_video_id_from_message(msg) or _parse_generic_id_from_message(msg)
            if vid and len(vid) == 11:
                youtube_vid = vid
                external_ids[logical] = vid
        else:
            ext_id = _parse_generic_id_from_message(msg)
            if ext_id:
                external_ids[logical] = ext_id

    expected = {
        logical_platform_for_upload_post(str(p).strip().upper(), post_platforms) for p in upload_post_platforms
    }
    filled = all(bool(str(external_ids.get(code) or "").strip()) for code in expected)
    return youtube_vid, filled


def apply_external_ids_patch(external_ids: dict[str, Any], patch: dict[str, Any] | None) -> None:
    if not patch:
        return
    for k, v in patch.items():
        if v in ("", None) and k == EXT_UPLOAD_POST_RECONCILIATION_STATE:
            external_ids.pop(k, None)
            continue
        external_ids[k] = v
