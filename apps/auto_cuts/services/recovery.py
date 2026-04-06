from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.utils import timezone

from apps.auto_cuts.models import (
    AutoCutAnalysis,
    AutoCutCorte,
    AutoCutReadyChunk,
    AutoCutSuggestion,
)
from apps.auto_cuts.services.extract import extract_corte
from apps.auto_cuts.services.video_chunks import cleanup_cortes_processo
from apps.jobs.models import VideoInventoryItem

logger = logging.getLogger(__name__)

DEFAULT_STUCK_AFTER = timedelta(hours=2)
DEFAULT_RECOVERY_COOLDOWN = timedelta(minutes=15)
RECOVERABLE_ANALYSIS_STATUSES = ("pending", "transcribing", "analyzing", "finalizing", "done")
EARLY_STAGE_STATUSES = {"pending", "transcribing", "analyzing"}
AUTO_RECOVERABLE_INVENTORY_STATUSES = {"AVAILABLE", "FAILED"}
BLOCKING_INVENTORY_STATUSES = {"SCHEDULED", "POSTING", "POSTED"}
MANUAL_ATTENTION_PREFIX = "Recovery manual required:"


class AutoCutRecoveryStage(StrEnum):
    FETCH_PREPARE = "fetch_prepare"
    TRANSCRIPTION = "transcription"
    AI_ANALYSIS = "ai_analysis"
    CUT_GENERATION = "cut_generation"
    FINALIZATION = "finalization"
    INVENTORY_SYNC = "inventory_sync"
    COMPLETED = "completed"
    AMBIGUOUS = "ambiguous"


class AutoCutRecoveryAction(StrEnum):
    IGNORE = "ignore"
    RESTART_FROM_BEGINNING = "restart_from_beginning"
    RERUN_FINALIZATION = "rerun_finalization"
    RERUN_INVENTORY_SYNC = "rerun_inventory_sync"
    MARK_MANUAL_ATTENTION = "mark_manual_attention"


@dataclass(frozen=True)
class AutoCutRecoveryPolicy:
    resumable_from_here: bool
    restart_from_beginning: bool
    requires_cleanup_before_rerun: bool
    cleanup_action: str


@dataclass(frozen=True)
class AutoCutRecoveryDecision:
    stage: AutoCutRecoveryStage
    action: AutoCutRecoveryAction
    reason: str
    policy: AutoCutRecoveryPolicy


@dataclass(frozen=True)
class AutoCutRecoveryResult:
    analysis_id: int
    status_before: str
    stage: str
    action: str
    reason: str


RECOVERY_POLICY_BY_STAGE: dict[AutoCutRecoveryStage, AutoCutRecoveryPolicy] = {
    AutoCutRecoveryStage.FETCH_PREPARE: AutoCutRecoveryPolicy(
        resumable_from_here=False,
        restart_from_beginning=True,
        requires_cleanup_before_rerun=True,
        cleanup_action="reset_generated_analysis_outputs",
    ),
    AutoCutRecoveryStage.TRANSCRIPTION: AutoCutRecoveryPolicy(
        resumable_from_here=False,
        restart_from_beginning=True,
        requires_cleanup_before_rerun=True,
        cleanup_action="reset_generated_analysis_outputs",
    ),
    AutoCutRecoveryStage.AI_ANALYSIS: AutoCutRecoveryPolicy(
        resumable_from_here=False,
        restart_from_beginning=True,
        requires_cleanup_before_rerun=True,
        cleanup_action="reset_generated_analysis_outputs",
    ),
    AutoCutRecoveryStage.CUT_GENERATION: AutoCutRecoveryPolicy(
        resumable_from_here=False,
        restart_from_beginning=True,
        requires_cleanup_before_rerun=True,
        cleanup_action="reset_generated_analysis_outputs",
    ),
    AutoCutRecoveryStage.FINALIZATION: AutoCutRecoveryPolicy(
        resumable_from_here=True,
        restart_from_beginning=False,
        requires_cleanup_before_rerun=True,
        cleanup_action="restore_base_cut_media_and_rerun_finalization",
    ),
    AutoCutRecoveryStage.INVENTORY_SYNC: AutoCutRecoveryPolicy(
        resumable_from_here=True,
        restart_from_beginning=False,
        requires_cleanup_before_rerun=False,
        cleanup_action="rerun_inventory_sync_only",
    ),
    AutoCutRecoveryStage.COMPLETED: AutoCutRecoveryPolicy(
        resumable_from_here=False,
        restart_from_beginning=False,
        requires_cleanup_before_rerun=False,
        cleanup_action="none",
    ),
    AutoCutRecoveryStage.AMBIGUOUS: AutoCutRecoveryPolicy(
        resumable_from_here=False,
        restart_from_beginning=False,
        requires_cleanup_before_rerun=False,
        cleanup_action="manual_attention_only",
    ),
}


def _analysis_video_path(analysis: AutoCutAnalysis) -> Path | None:
    video_file = getattr(analysis, "video_file", None)
    if not video_file:
        return None
    try:
        path = Path(video_file.path)
    except Exception:
        return None
    return path


def _file_path(file_field) -> Path | None:
    if not file_field:
        return None
    try:
        path = Path(file_field.path)
    except Exception:
        return None
    return path


def _path_exists(path: Path | None) -> bool:
    return bool(path and path.exists())


def _safe_delete_file(file_field) -> None:
    path = _file_path(file_field)
    try:
        if file_field:
            file_field.delete(save=False)
    except Exception:
        pass
    if path and path.exists():
        try:
            path.unlink()
        except Exception:
            pass


def _selected_cortes(analysis: AutoCutAnalysis) -> list[AutoCutCorte]:
    return list(
        AutoCutCorte.objects.filter(analysis=analysis, user_wants_finalize=True).select_related(
            "suggestion"
        )
    )


def _analysis_inputs_available(analysis: AutoCutAnalysis) -> bool:
    if bool(getattr(analysis, "is_ready_cuts", False)):
        chunks = list(AutoCutReadyChunk.objects.filter(analysis=analysis))
        return bool(chunks) and all(_path_exists(_file_path(ch.file)) for ch in chunks)
    if _path_exists(_analysis_video_path(analysis)):
        return True
    return bool((getattr(analysis, "youtube_url", "") or "").strip())


def _inventory_items_for_cortes(cortes: list[AutoCutCorte]) -> list[VideoInventoryItem]:
    corte_ids = [corte.id for corte in cortes if corte.id]
    if not corte_ids:
        return []
    return list(VideoInventoryItem.objects.filter(auto_cut_corte_id__in=corte_ids))


def _blocking_inventory_items(cortes: list[AutoCutCorte]) -> list[VideoInventoryItem]:
    return [
        item
        for item in _inventory_items_for_cortes(cortes)
        if item.status in BLOCKING_INVENTORY_STATUSES
    ]


def _corte_has_final_media(corte: AutoCutCorte) -> bool:
    return bool(corte.is_finalized and _path_exists(_file_path(corte.file)))


def _analysis_has_inventory_gap(cortes: list[AutoCutCorte]) -> bool:
    items_by_corte = {item.auto_cut_corte_id: item for item in _inventory_items_for_cortes(cortes)}
    for corte in cortes:
        if not _corte_has_final_media(corte):
            return False
        item = items_by_corte.get(corte.id)
        if item is None or item.status == "FAILED":
            return True
    return False


def _ready_chunk_path_for_suggestion(
    analysis: AutoCutAnalysis, suggestion: AutoCutSuggestion
) -> Path | None:
    raw = (getattr(suggestion, "source_asset_id", "") or "").strip()
    if not raw.startswith("ready_chunk:"):
        return None
    try:
        ready_chunk_id = int(raw.split(":", 1)[1])
    except (TypeError, ValueError):
        return None
    chunk = AutoCutReadyChunk.objects.filter(id=ready_chunk_id, analysis=analysis).first()
    if not chunk:
        return None
    return _file_path(chunk.file)


def _long_concat_source_path(analysis: AutoCutAnalysis, suggestion: AutoCutSuggestion) -> Path | None:
    raw = (getattr(suggestion, "source_asset_id", "") or "").strip()
    if raw != f"analysis:{analysis.id}:long":
        return None
    return Path(settings.MEDIA_ROOT) / "auto_cuts" / "cortes" / f"job_{analysis.id}_long_concat.mp4"


def _can_restore_corte_base(corte: AutoCutCorte) -> bool:
    analysis = corte.analysis
    suggestion = corte.suggestion

    ready_chunk_path = _ready_chunk_path_for_suggestion(analysis, suggestion)
    if _path_exists(ready_chunk_path):
        return True

    long_concat_path = _long_concat_source_path(analysis, suggestion)
    if _path_exists(long_concat_path):
        return True

    return _path_exists(_analysis_video_path(analysis))


def _can_rerun_finalization(analysis: AutoCutAnalysis, cortes: list[AutoCutCorte]) -> bool:
    return bool(cortes) and all(_can_restore_corte_base(corte) for corte in cortes)


def _mark_analysis_done(analysis: AutoCutAnalysis) -> None:
    analysis.status = "done"
    analysis.progress_message = "Concluído"
    analysis.progress = 100
    analysis.error = ""
    analysis.save(update_fields=["status", "progress_message", "progress", "error"])
    try:
        from apps.auto_cuts.services.youtube_fetch import register_manual_youtube_success

        register_manual_youtube_success(analysis)
    except Exception:
        logger.exception(
            "[RECOVERY] register_manual_youtube_success failed (analysis_id=%s)",
            analysis.id,
        )


def _mark_analysis_manual_attention(analysis: AutoCutAnalysis, reason: str) -> None:
    analysis.status = "error"
    analysis.progress_message = "Recovery interrompida. Revisao manual necessaria."
    analysis.error = f"{MANUAL_ATTENTION_PREFIX} {reason}"
    analysis.save(update_fields=["status", "progress_message", "error"])


def decide_analysis_recovery_action(analysis: AutoCutAnalysis) -> AutoCutRecoveryDecision:
    status = getattr(analysis, "status", "") or ""
    policy_stage = AutoCutRecoveryStage.AMBIGUOUS
    selected_cortes = _selected_cortes(analysis)
    blocking_items = _blocking_inventory_items(selected_cortes)

    if status == "done" and not selected_cortes:
        policy_stage = AutoCutRecoveryStage.COMPLETED
        return AutoCutRecoveryDecision(
            stage=policy_stage,
            action=AutoCutRecoveryAction.IGNORE,
            reason="Analysis concluida sem cortes pendentes de finalizacao.",
            policy=RECOVERY_POLICY_BY_STAGE[policy_stage],
        )

    if status in EARLY_STAGE_STATUSES:
        if status == "pending":
            policy_stage = AutoCutRecoveryStage.FETCH_PREPARE
        elif status == "transcribing":
            policy_stage = AutoCutRecoveryStage.TRANSCRIPTION
        elif selected_cortes or AutoCutSuggestion.objects.filter(analysis=analysis).exists():
            policy_stage = AutoCutRecoveryStage.CUT_GENERATION
        else:
            policy_stage = AutoCutRecoveryStage.AI_ANALYSIS
        if not _analysis_inputs_available(analysis):
            return AutoCutRecoveryDecision(
                stage=AutoCutRecoveryStage.AMBIGUOUS,
                action=AutoCutRecoveryAction.MARK_MANUAL_ATTENTION,
                reason="Nao ha entrada persistida suficiente para reiniciar a analysis com seguranca.",
                policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.AMBIGUOUS],
            )
        return AutoCutRecoveryDecision(
            stage=policy_stage,
            action=AutoCutRecoveryAction.RESTART_FROM_BEGINNING,
            reason="Stage inicial interrompido; reinicio conservador do fluxo completo.",
            policy=RECOVERY_POLICY_BY_STAGE[policy_stage],
        )

    if status == "error":
        return AutoCutRecoveryDecision(
            stage=AutoCutRecoveryStage.AMBIGUOUS,
            action=AutoCutRecoveryAction.MARK_MANUAL_ATTENTION,
            reason="Analysis ja esta em erro; recovery automatico nao e forcado sobre estado ambiguo.",
            policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.AMBIGUOUS],
        )

    if blocking_items:
        return AutoCutRecoveryDecision(
            stage=AutoCutRecoveryStage.AMBIGUOUS,
            action=AutoCutRecoveryAction.MARK_MANUAL_ATTENTION,
            reason=(
                "Existem itens de inventario vinculados em estados nao reversiveis automaticamente "
                "(SCHEDULED/POSTING/POSTED)."
            ),
            policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.AMBIGUOUS],
        )

    if status in {"finalizing", "done"}:
        if not selected_cortes:
            return AutoCutRecoveryDecision(
                stage=AutoCutRecoveryStage.FINALIZATION,
                action=AutoCutRecoveryAction.RERUN_FINALIZATION,
                reason="Analysis sem cortes selecionados ficou antes do fechamento final do estado.",
                policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.FINALIZATION],
            )

        missing_finalization = any(not _corte_has_final_media(corte) for corte in selected_cortes)
        if missing_finalization:
            if _can_rerun_finalization(analysis, selected_cortes):
                return AutoCutRecoveryDecision(
                    stage=AutoCutRecoveryStage.FINALIZATION,
                    action=AutoCutRecoveryAction.RERUN_FINALIZATION,
                    reason="Cortes nao finalizaram por completo; reconstruir base e refinalizar com cleanup.",
                    policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.FINALIZATION],
                )
            if _analysis_inputs_available(analysis):
                return AutoCutRecoveryDecision(
                    stage=AutoCutRecoveryStage.FINALIZATION,
                    action=AutoCutRecoveryAction.RESTART_FROM_BEGINNING,
                    reason="Finalizacao nao pode ser retomada com seguranca; reinicio conservador desde o inicio.",
                    policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.FINALIZATION],
                )
            return AutoCutRecoveryDecision(
                stage=AutoCutRecoveryStage.AMBIGUOUS,
                action=AutoCutRecoveryAction.MARK_MANUAL_ATTENTION,
                reason="Finalizacao incompleta sem base segura para reconstruir ou reiniciar.",
                policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.AMBIGUOUS],
            )

        if _analysis_has_inventory_gap(selected_cortes):
            return AutoCutRecoveryDecision(
                stage=AutoCutRecoveryStage.INVENTORY_SYNC,
                action=AutoCutRecoveryAction.RERUN_INVENTORY_SYNC,
                reason="Midia final existe, mas inventario ainda nao esta consistente; ressincronizar apenas o inventario.",
                policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.INVENTORY_SYNC],
            )

        if status == "finalizing":
            return AutoCutRecoveryDecision(
                stage=AutoCutRecoveryStage.INVENTORY_SYNC,
                action=AutoCutRecoveryAction.RERUN_INVENTORY_SYNC,
                reason="Cortes e inventario parecem saudaveis; faltou apenas consolidar o estado final da analysis.",
                policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.INVENTORY_SYNC],
            )

        return AutoCutRecoveryDecision(
            stage=AutoCutRecoveryStage.COMPLETED,
            action=AutoCutRecoveryAction.IGNORE,
            reason="Analysis ja esta saudavel e concluida.",
            policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.COMPLETED],
        )

    return AutoCutRecoveryDecision(
        stage=AutoCutRecoveryStage.AMBIGUOUS,
        action=AutoCutRecoveryAction.MARK_MANUAL_ATTENTION,
        reason=f"Status nao reconhecido para recovery automatico: {status!r}.",
        policy=RECOVERY_POLICY_BY_STAGE[AutoCutRecoveryStage.AMBIGUOUS],
    )


def _restore_corte_base_media(corte: AutoCutCorte) -> None:
    analysis = corte.analysis
    suggestion = corte.suggestion
    ready_chunk_path = _ready_chunk_path_for_suggestion(analysis, suggestion)
    long_concat_path = _long_concat_source_path(analysis, suggestion)
    analysis_video_path = _analysis_video_path(analysis)

    with tempfile.TemporaryDirectory() as tmpdir:
        restored_path = Path(tmpdir) / "restored_base.mp4"
        if _path_exists(ready_chunk_path):
            shutil.copy2(ready_chunk_path, restored_path)
        elif _path_exists(long_concat_path):
            shutil.copy2(long_concat_path, restored_path)
        elif _path_exists(analysis_video_path):
            if bool(getattr(analysis, "is_ready_cuts", False)):
                shutil.copy2(analysis_video_path, restored_path)
            else:
                extract_corte(
                    analysis_video_path,
                    suggestion.start_tc,
                    suggestion.end_tc,
                    restored_path,
                    use_gpu=False,
                )
        else:
            raise ValueError(f"Corte {corte.id} nao possui fonte segura para reconstruir a midia base.")

        _safe_delete_file(corte.file)
        corte.is_finalized = False
        with open(restored_path, "rb") as restored_file:
            corte.file.save(
                f"job_{analysis.id}_sug_{corte.suggestion_id}.mp4",
                File(restored_file),
                save=False,
            )
        corte.save(update_fields=["file", "is_finalized"])


def cleanup_finalization_artifacts(analysis: AutoCutAnalysis) -> dict[str, int]:
    selected_cortes = _selected_cortes(analysis)
    blocking_items = _blocking_inventory_items(selected_cortes)
    if blocking_items:
        raise ValueError(
            "Recovery de finalizacao nao e seguro com inventario em estados SCHEDULED/POSTING/POSTED."
        )

    summary = {"inventory_preserved": 0, "cortes_restored": 0}
    for item in _inventory_items_for_cortes(selected_cortes):
        if item.status in AUTO_RECOVERABLE_INVENTORY_STATUSES:
            summary["inventory_preserved"] += 1
    for corte in selected_cortes:
        _restore_corte_base_media(corte)
        summary["cortes_restored"] += 1

    return summary


def _cleanup_generated_cortes_dir(analysis: AutoCutAnalysis) -> int:
    deleted = 0
    cortes_dir = Path(settings.MEDIA_ROOT) / "auto_cuts" / "cortes"
    if not cortes_dir.exists():
        return deleted
    for file_path in cortes_dir.glob(f"job_{analysis.id}_*.mp4"):
        if file_path.exists():
            try:
                file_path.unlink()
                deleted += 1
            except OSError:
                logger.warning(
                    "[RECOVERY] Failed to delete generated cut artifact %s for analysis=%s",
                    file_path,
                    analysis.id,
                )
    return deleted


def cleanup_analysis_restart_artifacts(analysis: AutoCutAnalysis) -> dict[str, int]:
    cortes = list(AutoCutCorte.objects.filter(analysis=analysis))
    blocking_items = _blocking_inventory_items(cortes)
    if blocking_items:
        raise ValueError(
            "Restart from beginning is unsafe because inventory already advanced beyond AVAILABLE/FAILED."
        )

    summary = {
        "inventory_deleted": 0,
        "corte_files_deleted": 0,
        "thumbnail_files_deleted": 0,
        "cortes_deleted": 0,
        "suggestions_deleted": 0,
        "generated_cut_files_deleted": 0,
        "ready_chunks_reset": 0,
    }

    for item in _inventory_items_for_cortes(cortes):
        if item.status in AUTO_RECOVERABLE_INVENTORY_STATUSES:
            item.delete()
            summary["inventory_deleted"] += 1

    for corte in cortes:
        if corte.file:
            summary["corte_files_deleted"] += 1
        if corte.thumbnail:
            summary["thumbnail_files_deleted"] += 1
        _safe_delete_file(corte.file)
        _safe_delete_file(corte.thumbnail)

    corte_ids = [corte.id for corte in cortes]
    if corte_ids:
        deleted_by_type = AutoCutCorte.objects.filter(id__in=corte_ids).delete()[1]
        summary["cortes_deleted"] = deleted_by_type.get("auto_cuts.AutoCutCorte", 0)

    suggestion_ids = list(
        AutoCutSuggestion.objects.filter(analysis=analysis).values_list("id", flat=True)
    )
    if suggestion_ids:
        deleted_by_type = AutoCutSuggestion.objects.filter(id__in=suggestion_ids).delete()[1]
        summary["suggestions_deleted"] = deleted_by_type.get("auto_cuts.AutoCutSuggestion", 0)

    for ready_chunk in AutoCutReadyChunk.objects.filter(analysis=analysis):
        if any(
            [
                ready_chunk.duration_seconds is not None,
                bool(ready_chunk.transcript),
                ready_chunk.transcript_segments is not None,
            ]
        ):
            ready_chunk.duration_seconds = None
            ready_chunk.transcript = ""
            ready_chunk.transcript_segments = None
            ready_chunk.save(update_fields=["duration_seconds", "transcript", "transcript_segments"])
            summary["ready_chunks_reset"] += 1

    cleanup_cortes_processo(analysis.id)
    summary["generated_cut_files_deleted"] = _cleanup_generated_cortes_dir(analysis)
    return summary


def rerun_analysis_from_start(analysis: AutoCutAnalysis) -> dict[str, int]:
    summary = cleanup_analysis_restart_artifacts(analysis)
    analysis.status = "pending"
    analysis.progress = 0
    analysis.progress_message = "Recovery: reiniciando analysis desde o inicio..."
    analysis.transcript = ""
    analysis.transcript_segments = None
    analysis.error = ""
    analysis.save(
        update_fields=[
            "status",
            "progress",
            "progress_message",
            "transcript",
            "transcript_segments",
            "error",
        ]
    )

    from apps.auto_cuts.tasks import analyze_auto_cuts_task

    analyze_auto_cuts_task.delay(analysis.id)
    return summary


def _resolve_vertical_mode_for_recovery(analysis: AutoCutAnalysis) -> str:
    value = (getattr(analysis, "vertical_mode", None) or "").strip()
    if value:
        return value
    brand = getattr(analysis, "brand", None)
    return getattr(brand, "vertical_mode", None) or "zoom_crop"


def rerun_analysis_finalization(analysis: AutoCutAnalysis) -> dict[str, int]:
    summary = cleanup_finalization_artifacts(analysis)
    analysis.status = "finalizing"
    analysis.progress = max(95, min(99, int(getattr(analysis, "progress", 0) or 0)))
    analysis.progress_message = "Recovery: refinalizando cortes..."
    analysis.error = ""
    analysis.save(update_fields=["status", "progress", "progress_message", "error"])

    from apps.auto_cuts.tasks import finalizar_auto_cut_task

    finalizar_auto_cut_task.apply_async(
        args=[analysis.id],
        kwargs={
            "vertical_mode": _resolve_vertical_mode_for_recovery(analysis),
            "horizontal_logo_x": 20,
            "horizontal_logo_y": 20,
        },
        queue=settings.CELERY_QUEUE_RENDER,
    )
    return summary


def rerun_analysis_inventory_sync(analysis: AutoCutAnalysis) -> dict[str, int]:
    from apps.auto_cuts.tasks import _sync_inventory_item_from_corte

    synced = 0
    for corte in _selected_cortes(analysis):
        if not _corte_has_final_media(corte):
            raise ValueError(
                f"Corte {corte.id} ainda nao possui midia final consistente para ressincronizar inventario."
            )
        _sync_inventory_item_from_corte(corte)
        synced += 1

    _mark_analysis_done(analysis)
    return {"inventory_synced": synced}


def recover_autocut_analysis(
    analysis: AutoCutAnalysis,
    *,
    now=None,
    cooldown: timedelta = DEFAULT_RECOVERY_COOLDOWN,
    dry_run: bool = False,
    force: bool = False,
) -> AutoCutRecoveryResult:
    current_now = now or timezone.now()
    status_before = analysis.status
    decision = decide_analysis_recovery_action(analysis)

    if decision.action == AutoCutRecoveryAction.IGNORE:
        return AutoCutRecoveryResult(
            analysis_id=analysis.id,
            status_before=status_before,
            stage=decision.stage.value,
            action=decision.action.value,
            reason=decision.reason,
        )

    if not force and analysis.updated_at and analysis.updated_at > (current_now - cooldown):
        return AutoCutRecoveryResult(
            analysis_id=analysis.id,
            status_before=status_before,
            stage=decision.stage.value,
            action=AutoCutRecoveryAction.IGNORE.value,
            reason="Cooldown de recovery ainda ativo para esta analysis.",
        )

    logger.warning(
        "[RECOVERY] analysis=%s status=%s stage=%s action=%s reason=%s",
        analysis.id,
        status_before,
        decision.stage.value,
        decision.action.value,
        decision.reason,
    )

    if dry_run:
        return AutoCutRecoveryResult(
            analysis_id=analysis.id,
            status_before=status_before,
            stage=decision.stage.value,
            action=decision.action.value,
            reason=decision.reason,
        )

    if decision.action == AutoCutRecoveryAction.RESTART_FROM_BEGINNING:
        rerun_analysis_from_start(analysis)
    elif decision.action == AutoCutRecoveryAction.RERUN_FINALIZATION:
        rerun_analysis_finalization(analysis)
    elif decision.action == AutoCutRecoveryAction.RERUN_INVENTORY_SYNC:
        rerun_analysis_inventory_sync(analysis)
    else:
        _mark_analysis_manual_attention(analysis, decision.reason)

    return AutoCutRecoveryResult(
        analysis_id=analysis.id,
        status_before=status_before,
        stage=decision.stage.value,
        action=decision.action.value,
        reason=decision.reason,
    )


def detect_recoverable_analyses(
    *,
    now=None,
    stuck_after: timedelta = DEFAULT_STUCK_AFTER,
    limit: int | None = None,
) -> list[AutoCutAnalysis]:
    current_now = now or timezone.now()
    cutoff = current_now - stuck_after
    qs = AutoCutAnalysis.objects.filter(
        status__in=RECOVERABLE_ANALYSIS_STATUSES,
        updated_at__lte=cutoff,
    ).order_by("updated_at", "id")
    if limit:
        qs = qs[:limit]
    return list(qs)


def recover_stuck_autocut_analyses(
    *,
    now=None,
    stuck_after: timedelta = DEFAULT_STUCK_AFTER,
    cooldown: timedelta = DEFAULT_RECOVERY_COOLDOWN,
    limit: int | None = None,
    analysis_id: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[AutoCutRecoveryResult]:
    current_now = now or timezone.now()
    if analysis_id is not None:
        analyses = list(AutoCutAnalysis.objects.filter(id=analysis_id))
    else:
        analyses = detect_recoverable_analyses(
            now=current_now,
            stuck_after=stuck_after,
            limit=limit,
        )

    results: list[AutoCutRecoveryResult] = []
    for analysis in analyses:
        results.append(
            recover_autocut_analysis(
                analysis,
                now=current_now,
                cooldown=cooldown,
                dry_run=dry_run,
                force=force,
            )
        )
    return results
