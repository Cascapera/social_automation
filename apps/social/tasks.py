"""Tasks de postagem em redes sociais."""
import hashlib
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.jobs.models import ScheduledPost

logger = logging.getLogger(__name__)
YOUTUBE_PLATFORM_CODES = {"YT", "YTB"}


def _cleanup_local_media_if_possible(post: ScheduledPost) -> None:
    """
    Remove arquivos locais após postagem concluída para economizar armazenamento.
    Só limpa quando não há outros agendamentos ativos para a mesma origem.
    """
    try:
        active_statuses = ["PENDING", "POSTING"]
        if post.job_id:
            has_other_active = ScheduledPost.objects.filter(
                job_id=post.job_id,
                status__in=active_statuses,
            ).exclude(id=post.id).exists()
            if has_other_active:
                return
            try:
                output = post.job.output
            except Exception:
                output = None
            if output and output.file:
                output.file.delete(save=True)
            return

        if post.auto_cut_corte_id:
            has_other_active = ScheduledPost.objects.filter(
                auto_cut_corte_id=post.auto_cut_corte_id,
                status__in=active_statuses,
            ).exclude(id=post.id).exists()
            if has_other_active:
                return
            corte = post.auto_cut_corte
            if corte and corte.file:
                corte.file.delete(save=False)
                corte.file = None
            if corte and getattr(corte, "thumbnail", None):
                corte.thumbnail.delete(save=False)
                corte.thumbnail = None
            if corte:
                corte.save(update_fields=["file", "thumbnail"])
    except Exception:
        logger.exception("Falha ao limpar mídias locais do ScheduledPost=%s", post.id)


def _platforms_are_youtube_only(platforms) -> bool:
    codes = {str(code).strip().upper() for code in (platforms or []) if str(code).strip()}
    return bool(codes) and codes.issubset(YOUTUBE_PLATFORM_CODES)


@shared_task
def check_scheduled_posts_task():
    """
    Roda a cada minuto via Beat.
    - Fluxo padrão: publica PENDING quando scheduled_at <= now.
    - YouTube-only (YT/YTB): antecipa upload mesmo com data futura para usar publishAt.
    """
    now = timezone.now()
    due_posts = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__lte=now,
    ).select_related("job", "job__brand", "social_account")
    # Antecipação para YouTube: sobe privado e deixa publishAt no YouTube.
    future_candidates = ScheduledPost.objects.filter(
        status="PENDING",
        scheduled_at__gt=now + timedelta(seconds=30),
    ).select_related("job", "job__brand", "social_account")
    post_ids = {post.id for post in due_posts}
    for post in future_candidates:
        if _platforms_are_youtube_only(post.platforms):
            post_ids.add(post.id)
    for post_id in post_ids:
        post_to_platforms_task.delay(post_id)
    return {
        "checked_due": due_posts.count(),
        "queued": len(post_ids),
        "queued_future_youtube": max(0, len(post_ids) - due_posts.count()),
    }


@shared_task(bind=True)
def post_to_platforms_task(self, scheduled_post_id: int):
    """Publica um ScheduledPost nas plataformas configuradas."""
    try:
        post = ScheduledPost.objects.select_related(
            "job",
            "job__brand",
            "social_account",
            "auto_cut_corte",
            "auto_cut_corte__analysis",
            "auto_cut_corte__suggestion",
        ).get(id=scheduled_post_id)
    except ScheduledPost.DoesNotExist:
        return {"error": "ScheduledPost não encontrado"}
    if post.status != "PENDING":
        return {"skipped": "status não é PENDING"}
    post.status = "POSTING"
    post.save(update_fields=["status"])
    brand = None
    video_path = ""
    job_obj = post.job

    if post.job_id:
        brand = post.job.brand
        if not brand:
            post.status = "FAILED"
            post.error = "Job sem marca"
            post.save(update_fields=["status", "error"])
            return {"error": "Job sem marca"}
        output = post.job.output
        if not output or not output.file:
            post.status = "FAILED"
            post.error = "Job sem vídeo final"
            post.save(update_fields=["status", "error"])
            return {"error": "Job sem vídeo final"}
        video_path = output.file.path
    elif post.auto_cut_corte_id:
        corte = post.auto_cut_corte
        brand = corte.analysis.brand if corte and corte.analysis_id else None
        if not brand:
            post.status = "FAILED"
            post.error = "AutoCut sem marca"
            post.save(update_fields=["status", "error"])
            return {"error": "AutoCut sem marca"}
        if not corte.file:
            post.status = "FAILED"
            post.error = "AutoCut sem vídeo finalizado"
            post.save(update_fields=["status", "error"])
            return {"error": "AutoCut sem vídeo finalizado"}
        video_path = corte.file.path
    else:
        post.status = "FAILED"
        post.error = "ScheduledPost sem origem (job/corte)"
        post.save(update_fields=["status", "error"])
        return {"error": "ScheduledPost sem origem"}

    errors = []
    warnings = []
    retryable_errors = []
    external_ids = dict(post.external_ids or {})
    upload_fingerprint = ""
    social_account_changed = False
    # Hash do arquivo para deduplicação por canal/plataforma.
    try:
        hasher = hashlib.sha256()
        with open(video_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                hasher.update(chunk)
        upload_fingerprint = hasher.hexdigest()
    except Exception:
        upload_fingerprint = ""

    for platform in post.platforms:
        account = post.social_account
        if not account or account.platform != platform:
            from apps.brands.models import BrandSocialAccount

            # YouTube Shorts (YT) e YouTube longos (YTB) usam o mesmo OAuth.
            # Se não houver conta no código exato, tenta o código alternativo.
            platform_candidates = [platform]
            if platform == "YT":
                platform_candidates.append("YTB")
            elif platform == "YTB":
                platform_candidates.append("YT")

            account = (
                BrandSocialAccount.objects.filter(
                    brand=brand,
                    platform__in=platform_candidates,
                )
                .order_by("id")
                .first()
            )
        if not account:
            errors.append(f"{platform}: nenhuma conta conectada")
            continue
        if not post.social_account_id:
            post.social_account = account
            social_account_changed = True
        # Deduplicação extra: evita upload duplicado acidental para mesmo canal/plataforma.
        if upload_fingerprint and platform in ("YT", "YTB"):
            done_posts = ScheduledPost.objects.filter(
                status="DONE",
                upload_fingerprint=upload_fingerprint,
            ).exclude(id=post.id).select_related("social_account")
            duplicated = False
            for done_post in done_posts:
                done_platforms = done_post.platforms or []
                same_platform = platform in done_platforms
                same_channel = (
                    done_post.social_account_id
                    and account.channel_id
                    and done_post.social_account.channel_id == account.channel_id
                )
                if same_platform and same_channel:
                    duplicated = True
                    break
            if duplicated:
                errors.append(f"{platform}: upload duplicado detectado (mesmo arquivo e canal)")
                continue
        from apps.social.publishers import get_publisher

        publisher = get_publisher(platform)
        if not publisher:
            errors.append(f"{platform}: publisher não implementado")
            continue
        try:
            result = publisher.publish(account, video_path, job_obj, scheduled_post=post)
            video_id = (result or {}).get("video_id")
            if video_id:
                external_ids[platform] = video_id
            warning = (result or {}).get("warning")
            if warning:
                warnings.append(f"{platform}: {warning}")
        except Exception as e:
            if getattr(e, "retriable", False):
                retryable_errors.append(f"{platform}: {e}")
            else:
                errors.append(f"{platform}: {e}")
    if retryable_errors and not errors:
        next_retry = int(post.retry_count or 0) + 1
        if next_retry > 5:
            errors.extend(retryable_errors)
        else:
            delay = min(3600, 60 * (2 ** (next_retry - 1)))
            msg = " ; ".join(retryable_errors)
            post.status = "PENDING"
            post.retry_count = next_retry
            post.scheduled_at = timezone.now() + timedelta(seconds=delay)
            post.error = f"Falha temporária (tentativa {next_retry}/5). Próxima tentativa em {delay}s. {msg}"
            post.upload_fingerprint = upload_fingerprint
            post.external_ids = external_ids
            retry_fields = [
                "status",
                "retry_count",
                "scheduled_at",
                "error",
                "upload_fingerprint",
                "external_ids",
            ]
            if social_account_changed:
                retry_fields.append("social_account")
            post.save(update_fields=retry_fields)
            return {
                "status": post.status,
                "retry_scheduled_in_seconds": delay,
                "errors": retryable_errors,
            }
    if errors:
        post.status = "FAILED"
        all_errors = errors + warnings
        post.error = "; ".join(all_errors)
    else:
        post.status = "DONE"
        post.retry_count = 0
        post.posted_at = timezone.now()
        if warnings:
            post.error = "; ".join(warnings)
    post.upload_fingerprint = upload_fingerprint
    post.external_ids = external_ids
    update_fields = ["status", "error", "posted_at", "upload_fingerprint", "external_ids", "retry_count"]
    if social_account_changed:
        update_fields.append("social_account")
    post.save(update_fields=update_fields)
    if post.status == "DONE":
        _cleanup_local_media_if_possible(post)
    return {"status": post.status, "errors": errors}
