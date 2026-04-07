"""
Agregações para o dashboard (métricas reais a partir de AutoCutAnalysis / AutoCutCorte).

Definições:
- Vídeo processado: AutoCutAnalysis com status "done" (fluxo concluiu com sucesso).
- Minutos processados: soma da duração de origem por job concluído — soma dos
  AutoCutReadyChunk.duration_seconds quando existir lote; caso contrário, o maior
  "end" em transcript_segments (mesma base usada na transcrição, alinhada ao áudio).
- Cortes finalizados: AutoCutCorte com is_finalized=True em análises concluídas (done).
"""

from __future__ import annotations

from django.db import connection
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutReadyChunk
from apps.brands.models import Brand, Factory


def user_scoped_auto_cut_qs(user):
    """Mesmo critério de AutoCutAnalysisViewSet.get_queryset: jobs do usuário ou auto-fetch."""
    return AutoCutAnalysis.objects.filter(Q(user=user) | Q(user__isnull=True))


def apply_scope(qs, brand_id: int | None, factory_id: int | None):
    if brand_id:
        return qs.filter(brand_id=brand_id)
    if factory_id:
        return qs.filter(brand__factory_id=factory_id)
    return qs.none()


def _max_end_seconds_from_segments(segments) -> float:
    if not segments or not isinstance(segments, list):
        return 0.0
    ends: list[float] = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        end = s.get("end")
        if end is None:
            continue
        try:
            ends.append(float(end))
        except (TypeError, ValueError):
            continue
    return max(ends) if ends else 0.0


def _sum_transcript_duration_seconds_no_chunks(done_qs_no_chunks) -> float:
    """
    Soma, por análise, o tempo máximo coberto pela transcrição (segundos).
    Para jobs sem ready_chunks (vídeo único ou fluxo padrão).
    """
    if connection.vendor == "postgresql":
        table = AutoCutAnalysis._meta.db_table
        chunk_table = AutoCutReadyChunk._meta.db_table
        ids = list(done_qs_no_chunks.values_list("pk", flat=True))
        if not ids:
            return 0.0
        total_pg = 0.0
        batch_size = 4000
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            placeholders = ",".join(["%s"] * len(batch))
            sql = f"""
                SELECT COALESCE(SUM(
                    (SELECT MAX((elem->>'end')::double precision)
                     FROM jsonb_array_elements(a.transcript_segments::jsonb) AS elem)
                ), 0)
                FROM {table} a
                WHERE a.id IN ({placeholders})
                AND NOT EXISTS (SELECT 1 FROM {chunk_table} c WHERE c.analysis_id = a.id)
                AND a.transcript_segments IS NOT NULL
            """
            with connection.cursor() as cursor:
                cursor.execute(sql, batch)
                row = cursor.fetchone()
                total_pg += float(row[0] or 0)
        return total_pg

    total = 0.0
    for a in done_qs_no_chunks.only("transcript_segments").iterator(chunk_size=500):
        total += _max_end_seconds_from_segments(a.transcript_segments)
    return total


def compute_dashboard_metrics(user, brand_id: int | None, factory_id: int | None) -> dict:
    base = apply_scope(user_scoped_auto_cut_qs(user), brand_id, factory_id)
    done = base.filter(status="done")
    done_pk_sq = done.values("pk")

    videos_processed = done.count()

    # Subquery: uma passagem SQL por agregação, sem materializar PKs em Python.
    chunk_sum = (
        AutoCutReadyChunk.objects.filter(analysis_id__in=Subquery(done_pk_sq)).aggregate(
            s=Sum("duration_seconds")
        )["s"]
        or 0.0
    )

    done_without_chunks = done.annotate(
        _has_chunk=Exists(AutoCutReadyChunk.objects.filter(analysis_id=OuterRef("pk")))
    ).filter(_has_chunk=False)

    transcript_sec = _sum_transcript_duration_seconds_no_chunks(done_without_chunks)

    total_seconds = float(chunk_sum) + float(transcript_sec)
    total_minutes = total_seconds / 60.0

    # is_finalized só é persistido True após o fluxo de finalização (tasks); só contamos cortes de análises done.
    finalized_cuts = AutoCutCorte.objects.filter(
        analysis_id__in=Subquery(done_pk_sq),
        is_finalized=True,
    ).count()

    avg_cuts_per_video = None
    if videos_processed > 0:
        avg_cuts_per_video = round(finalized_cuts / videos_processed, 4)

    active_brands = None
    active_factories = None
    if brand_id:
        active_brands = 1
        b = Brand.objects.filter(pk=brand_id).select_related("factory").first()
        if b and b.factory_id and b.factory:
            active_factories = 1 if b.factory.is_active else 0
        else:
            active_factories = 0
    elif factory_id:
        active_brands = Brand.objects.filter(factory_id=factory_id).count()
        active_factories = 1 if Factory.objects.filter(pk=factory_id, is_active=True).exists() else 0

    breakdown = None
    if factory_id and not brand_id:
        vis = Q(auto_cut_analyses__user=user) | Q(auto_cut_analyses__user__isnull=True)
        breakdown = list(
            Brand.objects.filter(factory_id=factory_id)
            .annotate(
                videos_done=Count(
                    "auto_cut_analyses",
                    filter=Q(auto_cut_analyses__status="done") & vis,
                    distinct=True,
                ),
                cuts_finalized=Count(
                    "auto_cut_analyses__cortes",
                    filter=Q(
                        auto_cut_analyses__status="done",
                        auto_cut_analyses__cortes__is_finalized=True,
                    )
                    & vis,
                    distinct=True,
                ),
            )
            .order_by("-videos_done", "name")
            .values("id", "name", "videos_done", "cuts_finalized")[:30]
        )

    return {
        "scope": {"brand_id": brand_id, "factory_id": factory_id},
        "videos_processed": videos_processed,
        "total_minutes_processed": round(total_minutes, 4),
        "total_seconds_processed": round(total_seconds, 4),
        "finalized_cuts": finalized_cuts,
        "active_brands": active_brands,
        "active_factories": active_factories,
        "avg_finalized_cuts_per_video": avg_cuts_per_video,
        "breakdown_by_brand": breakdown,
        "definitions": {
            "processed_video_status": "done",
            "finalized_cut_field": "is_finalized",
            "minutes_source": (
                "Soma de AutoCutReadyChunk.duration_seconds para jobs com chunks; "
                "caso contrário, máximo de transcript_segments[].end (transcrição) por análise."
            ),
        },
    }
