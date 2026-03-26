from django.contrib import admin

from .models import (
    Brand,
    BrandAsset,
    BrandSocialAccount,
    BrandYouTubeCredential,
    Factory,
    FactoryYouTubeCheckCredential,
    ProcessedChannelVideo,
    ProcessedYoutubeVideo,
    SearchChannel,
)


@admin.register(Factory)
class FactoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "timezone",
        "is_active",
        "scheduling_paused",
        "auto_fetch_enabled",
        "created_at",
    )
    list_filter = ("is_active", "scheduling_paused", "timezone")
    search_fields = ("name",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("brand_id_display", "name", "slug", "factory", "theme_category")
    list_filter = ("factory", "theme_category")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "factory", "theme_category")}),
        ("YouTube", {"fields": ("youtube_made_for_kids", "youtube_description_extra", "youtube_client_id", "youtube_client_secret", "youtube_redirect_uri")}),
        ("Thumbnails", {"fields": ("thumbnail_font", "thumbnail_band_color", "thumbnail_text_color", "thumbnail_effect_color")}),
        (
            "Agendamento",
            {
                "fields": (
                    "short_slot_times",
                    "long_slot_times",
                    "vertical_mode",
                    "long_video_subtitles_enabled",
                    "long_video_logo_enabled",
                ),
            },
        ),
        (
            "Upload-Post (TikTok, X, Instagram, YouTube)",
            {
                "fields": (
                    "upload_post_tiktok_enabled",
                    "upload_post_tiktok_extra_description",
                    "upload_post_x_enabled",
                    "upload_post_x_extra_description",
                    "upload_post_instagram_enabled",
                    "upload_post_instagram_extra_description",
                    "upload_post_youtube_enabled",
                ),
            },
        ),
    )

    @admin.display(description="ID")
    def brand_id_display(self, obj):
        return f"brand_{obj.id}" if obj.id else "-"

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


@admin.register(SearchChannel)
class SearchChannelAdmin(admin.ModelAdmin):
    list_display = ("factory", "channel_title", "youtube_channel_id", "target_brand", "is_active", "last_checked_at")
    list_filter = ("factory", "is_active")
    search_fields = ("youtube_channel_url", "channel_title", "youtube_channel_id")


@admin.register(ProcessedYoutubeVideo)
class ProcessedYoutubeVideoAdmin(admin.ModelAdmin):
    list_display = ("factory", "youtube_video_id", "source", "processed_at")
    list_filter = ("factory", "source")
    list_display_links = ("youtube_video_id",)

    def has_delete_permission(self, request, obj=None):
        """Não permite apagar: registros devem persistir para evitar reprocessamento."""
        return False

    def has_add_permission(self, request):
        """Adição é feita automaticamente pelo sistema."""
        return False


@admin.register(ProcessedChannelVideo)
class ProcessedChannelVideoAdmin(admin.ModelAdmin):
    list_display = ("search_channel", "youtube_video_id", "factory", "processed_at")
    list_filter = ("factory",)


@admin.register(FactoryYouTubeCheckCredential)
class FactoryYouTubeCheckCredentialAdmin(admin.ModelAdmin):
    list_display = ("factory", "account_name", "channel_id", "updated_at")
