"""Celery tasks de auto_cuts.

Só as tasks. O corpo dos dois fluxos mora em `services/analysis_flow.py` e
`services/finalization_flow.py` (refactor.md R-19).

Elas continuam aqui porque **nome de task Celery é contrato de fila**:
`apps.auto_cuts.tasks.analyze_auto_cuts_task` e `...finalizar_auto_cut_task` estão em
`settings.CELERY_TASK_ROUTES` e podem estar em mensagens ainda não consumidas. Mover a
task de módulo renomearia as duas coisas de uma vez.
"""

from celery import shared_task

from apps.auto_cuts.services.analysis_flow import run_analysis
from apps.auto_cuts.services.finalization_flow import run_finalization


@shared_task(bind=True)
def analyze_auto_cuts_task(self, analysis_id: int) -> None:
    """Transcribe, analyze in chunks, and aggregate viral cut suggestions."""
    run_analysis(self, analysis_id)


@shared_task(bind=True)
def finalizar_auto_cut_task(
    self,
    analysis_id: int,
    subtitle_style: dict | None = None,
    vertical_mode: str | None = None,
    background_color: str | None = None,
    custom_text: str | None = None,
    font_size_title: int | None = None,
    font_size_text: int | None = None,
    title_color: str | None = None,
    text_color: str | None = None,
    horizontal_insert_logo: bool = False,
    horizontal_logo_x: int | None = None,
    horizontal_logo_y: int | None = None,
    overlay_animation_asset_id: int | None = None,
    overlay_position: str | None = None,
    overlay_margin: int | None = None,
    overlay_height: int | None = None,
    long_overlay_enabled: bool | None = None,
    long_overlay_asset_id: int | None = None,
) -> None:
    """
    Finalize cuts: delete unselected, reframe verticals (if 16:9 source),
    burn subtitles on cuts with needs_subtitle, mark all as finalized.

    `horizontal_insert_logo` está na assinatura mas **nenhum código lê o valor** — a
    `apps/api/views.py` o envia, o corpo nunca o consultou. Fica aqui porque remover
    quebraria as mensagens já enfileiradas; ver L-9 no refactor.md.
    """
    run_finalization(
        self,
        analysis_id,
        subtitle_style=subtitle_style,
        vertical_mode=vertical_mode,
        background_color=background_color,
        custom_text=custom_text,
        font_size_title=font_size_title,
        font_size_text=font_size_text,
        title_color=title_color,
        text_color=text_color,
        horizontal_logo_x=horizontal_logo_x,
        horizontal_logo_y=horizontal_logo_y,
        overlay_animation_asset_id=overlay_animation_asset_id,
        overlay_position=overlay_position,
        overlay_margin=overlay_margin,
        overlay_height=overlay_height,
        long_overlay_enabled=long_overlay_enabled,
        long_overlay_asset_id=long_overlay_asset_id,
    )
