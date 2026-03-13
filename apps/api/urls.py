from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import (
    FactoryViewSet,
    SearchChannelViewSet,
    BrandViewSet,
    BrandAssetViewSet,
    BrandSocialAccountViewSet,
    BrandYouTubeCredentialViewSet,
    SourceVideoViewSet,
    CutViewSet,
    JobViewSet,
    ScheduledPostViewSet,
    RegisterViewSet,
    AutoCutAnalysisViewSet,
    AutoCutSuggestionViewSet,
    AutoCutCorteViewSet,
    VideoInventoryItemViewSet,
    FactoryPostingScheduleViewSet,
    PostedVideoLogViewSet,
)

router = DefaultRouter()
router.register("factories", FactoryViewSet, basename="factory")
router.register("search-channels", SearchChannelViewSet, basename="search-channel")
router.register("brands", BrandViewSet, basename="brand")
router.register("brand-assets", BrandAssetViewSet, basename="brand-asset")
router.register("social-accounts", BrandSocialAccountViewSet, basename="social-account")
router.register("brand-youtube-credentials", BrandYouTubeCredentialViewSet, basename="brand-youtube-credential")
router.register("sources", SourceVideoViewSet, basename="source")
router.register("cuts", CutViewSet, basename="cut")
router.register("jobs", JobViewSet, basename="job")
router.register("scheduled-posts", ScheduledPostViewSet, basename="scheduled-post")
router.register("register", RegisterViewSet, basename="register")
router.register("auto-cuts", AutoCutAnalysisViewSet, basename="auto-cut")
router.register("auto-cut-suggestions", AutoCutSuggestionViewSet, basename="auto-cut-suggestion")
router.register("auto-cut-cortes", AutoCutCorteViewSet, basename="auto-cut-corte")
router.register("video-inventory", VideoInventoryItemViewSet, basename="video-inventory")
router.register("factory-schedules", FactoryPostingScheduleViewSet, basename="factory-schedule")
router.register("posted-videos", PostedVideoLogViewSet, basename="posted-videos")

urlpatterns = [
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("youtube/", include("apps.social.urls")),
    path("", include(router.urls)),
]
