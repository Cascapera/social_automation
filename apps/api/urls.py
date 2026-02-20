from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import (
    BrandViewSet,
    BrandAssetViewSet,
    SourceVideoViewSet,
    CutViewSet,
    JobViewSet,
    ScheduledPostViewSet,
    RegisterViewSet,
)

router = DefaultRouter()
router.register("brands", BrandViewSet, basename="brand")
router.register("brand-assets", BrandAssetViewSet, basename="brand-asset")
router.register("sources", SourceVideoViewSet, basename="source")
router.register("cuts", CutViewSet, basename="cut")
router.register("jobs", JobViewSet, basename="job")
router.register("scheduled-posts", ScheduledPostViewSet, basename="scheduled-post")
router.register("register", RegisterViewSet, basename="register")

urlpatterns = [
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("", include(router.urls)),
]
