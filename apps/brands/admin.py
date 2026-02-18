from django.contrib import admin
from .models import Brand, BrandAsset

@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(BrandAsset)
class BrandAssetAdmin(admin.ModelAdmin):
    list_display = ("brand", "asset_type", "label", "file")
    list_filter = ("brand", "asset_type")
