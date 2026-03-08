from django.contrib import admin
from .models import Factory, Brand, BrandAsset, BrandSocialAccount, BrandYouTubeCredential


@admin.register(Factory)
class FactoryAdmin(admin.ModelAdmin):
    list_display = ("name", "timezone", "is_active", "scheduling_paused", "created_at")
    list_filter = ("is_active", "scheduling_paused", "timezone")
    search_fields = ("name",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "factory", "theme_category")
    list_filter = ("factory", "theme_category")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(BrandAsset)
class BrandAssetAdmin(admin.ModelAdmin):
    list_display = ("brand", "asset_type", "label", "file")
    list_filter = ("brand", "asset_type")


@admin.register(BrandSocialAccount)
class BrandSocialAccountAdmin(admin.ModelAdmin):
    list_display = ("brand", "platform", "channel_id", "account_name", "created_at")
    list_filter = ("brand", "platform")


@admin.register(BrandYouTubeCredential)
class BrandYouTubeCredentialAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "order_index",
        "label",
        "is_active",
        "channel_id",
        "account_name",
        "quota_exceeded_until",
        "updated_at",
    )
    list_filter = ("brand", "is_active")
    search_fields = ("brand__name", "label", "channel_id", "account_name")
