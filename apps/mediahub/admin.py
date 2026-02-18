from django.contrib import admin
from .models import SourceVideo

@admin.register(SourceVideo)
class SourceVideoAdmin(admin.ModelAdmin):
    list_display = ("id", "brand", "title", "created_at")
    list_filter = ("brand", "created_at")
    search_fields = ("title",)
