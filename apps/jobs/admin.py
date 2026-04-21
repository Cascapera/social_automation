from django import forms
from django.contrib import admin, messages

from .models import (
    DailyPostingPlan,
    DailyPostingPlanItem,
    DeadLetterJob,
    FactoryPostingAttemptLog,
    FactoryPostingSchedule,
    FactoryScheduleRun,
    Job,
    JobCut,
    PipelineExecution,
    PostedVideoLog,
    RenderOutput,
    ScheduledPost,
    StageExecution,
    VideoInventoryItem,
)


class JobCutInline(admin.TabularInline):
    model = JobCut
    extra = 1
    ordering = ["id"]
    autocomplete_fields = ["cut"]
    exclude = ["order"]  # ordem = id (ordem de adição)
    verbose_name = "Corte"
    verbose_name_plural = "Cortes (intro → corte1 → corte2 → ... → outro)"


class JobAdminForm(forms.ModelForm):
    """Select em vez de checkbox: valor sempre enviado no POST."""
    make_vertical = forms.TypedChoiceField(
        coerce=lambda x: x is True or str(x).lower() in ("true", "1"),
        choices=[(True, "Sim (vertical 9:16)"), (False, "Não (horizontal 16:9)")],
        widget=forms.RadioSelect,
    )
    target_platforms = forms.MultipleChoiceField(
        choices=Job.PLATFORM,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Redes para publicar",
        help_text="Marque as redes onde o vídeo será postado automaticamente ao final da automação.",
    )

    class Meta:
        model = Job
        exclude = ["cuts"]  # Gerenciado via JobCutInline

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.target_platforms:
            self.initial["target_platforms"] = self.instance.target_platforms
        elif not self.instance.pk:
            self.initial["target_platforms"] = ["YT"]  # default para novos jobs

    def clean_target_platforms(self):
        value = self.cleaned_data.get("target_platforms") or []
        platforms = list(value)
        if not platforms:
            raise forms.ValidationError("Selecione pelo menos uma rede para publicar.")
        return platforms


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    form = JobAdminForm
    inlines = [JobCutInline]
    list_display = ("id", "name", "archived", "status", "make_vertical", "transition", "target_platforms_display", "cuts_count", "created_at")

    def cuts_count(self, obj):
        return obj.job_cuts.count()
    cuts_count.short_description = "Cortes"

    def target_platforms_display(self, obj):
        if not obj.target_platforms:
            return "-"
        labels = dict(Job.PLATFORM)
        return ", ".join(labels.get(p, p) for p in obj.target_platforms)
    target_platforms_display.short_description = "Redes"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "make_vertical":
            kwargs["help_text"] = "Clique em SAVE após alterar. Depois enfileire o job."
        if db_field.name == "transition_duration":
            kwargs["help_text"] = "Duração em segundos (ex: 0.5, 1.0, 0.2). Usado quando transição ≠ Nenhuma."
        return super().formfield_for_dbfield(db_field, request, **kwargs)
    list_filter = ("status", "archived")
    search_fields = ("job_cuts__cut__source__title", "job_cuts__cut__name")
    readonly_fields = (
        "correlation_id",
        "status", "progress", "log", "error",
        "created_at", "started_at", "finished_at",
    )

    actions = ["enqueue_jobs"]

    @admin.action(description="Enfileirar (Celery) jobs selecionados")
    def enqueue_jobs(self, request, queryset):
        from .tasks import process_job
        for job in queryset.filter(status__in=["QUEUED", "FAILED", "DONE"]):
            process_job.delay(job.id)


class StageExecutionInline(admin.TabularInline):
    model = StageExecution
    extra = 0
    can_delete = False
    readonly_fields = (
        "stage_name",
        "status",
        "queue_name",
        "task_name",
        "retry_count",
        "started_at",
        "completed_at",
        "duration_ms",
        "input_payload",
        "output_payload",
        "error_class",
        "error_message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PipelineExecution)
class PipelineExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "pipeline_type",
        "aggregate_type",
        "aggregate_id",
        "attempt_number",
        "status",
        "current_stage",
        "correlation_id",
        "started_at",
        "completed_at",
        "updated_at",
    )
    list_filter = ("pipeline_type", "aggregate_type", "status", "attempt_number")
    search_fields = ("correlation_id", "aggregate_type", "=aggregate_id")
    readonly_fields = (
        "pipeline_type",
        "aggregate_type",
        "aggregate_id",
        "attempt_number",
        "correlation_id",
        "status",
        "current_stage",
        "started_at",
        "completed_at",
        "failure_reason",
        "metadata_json",
        "created_at",
        "updated_at",
    )
    inlines = [StageExecutionInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StageExecution)
class StageExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "pipeline_execution",
        "stage_name",
        "status",
        "queue_name",
        "retry_count",
        "started_at",
        "completed_at",
        "duration_ms",
    )
    list_filter = ("stage_name", "status", "queue_name")
    search_fields = (
        "pipeline_execution__correlation_id",
        "=pipeline_execution__aggregate_id",
        "stage_name",
        "task_name",
        "error_class",
        "error_message",
    )
    readonly_fields = (
        "pipeline_execution",
        "stage_name",
        "status",
        "queue_name",
        "task_name",
        "retry_count",
        "started_at",
        "completed_at",
        "duration_ms",
        "input_payload",
        "output_payload",
        "error_class",
        "error_message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DeadLetterJob)
class DeadLetterJobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_name", "aggregate_id", "status", "error_category", "created_at")
    list_filter = ("status", "error_category", "job_name")
    search_fields = ("correlation_id", "=aggregate_id")
    list_select_related = ("pipeline_execution", "stage_execution", "replayed_by")
    readonly_fields = (
        "pipeline_execution",
        "stage_execution",
        "aggregate_type",
        "aggregate_id",
        "job_name",
        "queue_name",
        "correlation_id",
        "payload_json",
        "error_class",
        "error_message",
        "error_category",
        "status",
        "retry_count",
        "first_failed_at",
        "last_failed_at",
        "replayed_at",
        "replayed_by",
        "replay_result_json",
        "created_at",
        "updated_at",
    )
    actions = ["replay_selected_dead_letters", "ignore_selected_dead_letters"]

    @admin.action(description="Replay selected dead letters")
    def replay_selected_dead_letters(self, request, queryset):
        from .services.dead_letter import replay_dead_letter_job

        replayed = 0
        failed = 0
        for dead_letter in queryset:
            try:
                replay_dead_letter_job(dead_letter, user=request.user)
            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    f"Dead letter {dead_letter.id} não pôde ser reenfileirada: {exc}",
                    level=messages.WARNING,
                )
            else:
                replayed += 1

        if replayed:
            self.message_user(
                request,
                f"{replayed} dead letter(s) reenfileirada(s) com sucesso.",
                level=messages.SUCCESS,
            )
        if failed and not replayed:
            self.message_user(
                request,
                "Nenhuma dead letter selecionada pôde ser reenfileirada.",
                level=messages.WARNING,
            )

    @admin.action(description="Mark selected dead letters as ignored")
    def ignore_selected_dead_letters(self, request, queryset):
        from .services.dead_letter import mark_dead_letter_ignored

        ignored = 0
        failed = 0
        for dead_letter in queryset:
            try:
                mark_dead_letter_ignored(dead_letter)
            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    f"Dead letter {dead_letter.id} não pôde ser ignorada: {exc}",
                    level=messages.WARNING,
                )
            else:
                ignored += 1

        if ignored:
            self.message_user(
                request,
                f"{ignored} dead letter(s) marcadas como ignoradas.",
                level=messages.SUCCESS,
            )
        if failed and not ignored:
            self.message_user(
                request,
                "Nenhuma dead letter selecionada pôde ser ignorada.",
                level=messages.WARNING,
            )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        context.update(
            {
                "show_save": False,
                "show_save_and_continue": False,
                "show_save_and_add_another": False,
                "show_save_as_new": False,
                "show_delete": False,
            }
        )
        return super().render_change_form(
            request,
            context,
            add=add,
            change=change,
            form_url=form_url,
            obj=obj,
        )


@admin.register(RenderOutput)
class RenderOutputAdmin(admin.ModelAdmin):
    list_display = ("job", "file", "created_at")


@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "scheduled_at", "status", "created_at")
    list_filter = ("status",)


@admin.register(VideoInventoryItem)
class VideoInventoryItemAdmin(admin.ModelAdmin):
    list_display = ("id", "factory", "brand", "video_type", "status", "virality_score", "source_asset_id", "created_at")
    list_filter = ("factory", "brand", "video_type", "status")
    search_fields = ("title", "source_asset_id")


class DailyPostingPlanItemInline(admin.TabularInline):
    model = DailyPostingPlanItem
    extra = 0
    readonly_fields = (
        "order_index",
        "video_type",
        "scheduled_at",
        "status",
        "inventory_item",
        "scheduled_post",
        "created_at",
        "updated_at",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DailyPostingPlan)
class DailyPostingPlanAdmin(admin.ModelAdmin):
    list_display = ("id", "brand", "plan_date", "timezone", "status", "planned_posts_count", "generated_at")
    list_filter = ("status", "timezone", "plan_date")
    search_fields = ("brand__name", "brand__slug")
    readonly_fields = ("generated_at", "config_snapshot", "last_error")
    inlines = [DailyPostingPlanItemInline]


@admin.register(DailyPostingPlanItem)
class DailyPostingPlanItemAdmin(admin.ModelAdmin):
    list_display = ("id", "plan", "order_index", "video_type", "scheduled_at", "status")
    list_filter = ("video_type", "status")


@admin.register(FactoryScheduleRun)
class FactoryScheduleRunAdmin(admin.ModelAdmin):
    list_display = ("id", "factory", "run_date", "timezone", "created_at")
    list_filter = ("factory", "timezone", "run_date")


@admin.register(FactoryPostingSchedule)
class FactoryPostingScheduleAdmin(admin.ModelAdmin):
    list_display = ("id", "factory", "brand", "video_type", "scheduled_at", "status", "daily_plan_item", "attempt_count")
    list_filter = ("factory", "brand", "video_type", "status")


@admin.register(FactoryPostingAttemptLog)
class FactoryPostingAttemptLogAdmin(admin.ModelAdmin):
    list_display = ("id", "posting_schedule", "attempt_number", "result", "started_at", "finished_at")
    list_filter = ("result",)


@admin.register(PostedVideoLog)
class PostedVideoLogAdmin(admin.ModelAdmin):
    list_display = ("id", "factory", "brand", "external_platform", "external_video_id", "posted_at")
    list_filter = ("factory", "brand", "external_platform")
