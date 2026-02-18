from django.contrib import admin
from .models import Cut

@admin.register(Cut)
class CutAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "name", "start_tc", "end_tc", "created_at")
    list_filter = ("created_at", "source__brand")
    search_fields = ("name", "source__title")
