from django.contrib import admin
from .models import Brand, BrandAsset, BrandSocialAccount


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(BrandAsset)
class BrandAssetAdmin(admin.ModelAdmin):
    list_display = ("brand", "asset_type", "label", "file")
    list_filter = ("brand", "asset_type")


@admin.register(BrandSocialAccount)
class BrandSocialAccountAdmin(admin.ModelAdmin):
    list_display = ("brand", "platform", "channel_id", "account_name", "created_at")
    list_filter = ("brand", "platform")
