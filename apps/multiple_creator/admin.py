from django.contrib import admin

from .models import MultipleCreatorBrandExecution, MultipleCreatorJob


class BrandExecutionInline(admin.TabularInline):
    model = MultipleCreatorBrandExecution
    extra = 0
    fields = ("brand", "status", "auto_cut_analysis", "error", "started_at", "finished_at")
    readonly_fields = ("started_at", "finished_at")


@admin.register(MultipleCreatorJob)
class MultipleCreatorJobAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "source_kind", "status", "progress", "user", "created_at")
    list_filter = ("status", "source_kind", "created_at")
    search_fields = ("name", "assunto", "convidados", "correlation_id")
    readonly_fields = ("created_at", "updated_at", "correlation_id")
    inlines = [BrandExecutionInline]


@admin.register(MultipleCreatorBrandExecution)
class MultipleCreatorBrandExecutionAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "brand", "status", "auto_cut_analysis", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("job__name", "brand__name")
    readonly_fields = ("created_at", "updated_at")
