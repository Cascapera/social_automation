from django.contrib import admin
from .models import AutoCutAnalysis, AutoCutSuggestion


@admin.register(AutoCutAnalysis)
class AutoCutAnalysisAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "brand", "created_at")
    list_filter = ("status",)
    search_fields = ("name",)


@admin.register(AutoCutSuggestion)
class AutoCutSuggestionAdmin(admin.ModelAdmin):
    list_display = ("id", "analysis", "cut_type", "title", "theme_category", "source_asset_id", "virality_score", "rank")
    list_filter = ("cut_type", "theme_category")
