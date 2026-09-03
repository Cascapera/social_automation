"""Views da API, por domínio (refactor.md R-15 / D-04).

Era um `views.py` de 2.332 linhas com 26 classes e 38 alterações por ano — toda mudança
de qualquer domínio conflitava com qualquer outra. O R-15 quebrou o arquivo em módulos por
domínio, **sem editar uma linha de corpo de método**.

Este `__init__` reexporta os nomes públicos para que `apps/api/urls.py` continue com o
mesmo import de antes. As rotas registradas foram comparadas uma a uma, antes e depois.
"""

from .auth import RegisterViewSet
from .auto_cuts import (
    AutoCutAnalysisViewSet,
    AutoCutCorteViewSet,
    AutoCutSuggestionViewSet,
)
from .brands import (
    BrandAssetViewSet,
    BrandCategoryViewSet,
    BrandSocialAccountViewSet,
    BrandViewSet,
    BrandYouTubeCredentialViewSet,
)
from .dashboards import (
    DashboardMetricsView,
    FactoryYoutubeDashboardView,
    FactoryYoutubeVideosView,
)
from .factories import FactoryViewSet, SearchChannelViewSet
from .jobs import JobViewSet
from .media import CutViewSet, SourceVideoViewSet
from .multiple_creator import MultipleCreatorViewSet
from .posting import (
    FactoryPostingScheduleViewSet,
    PostedVideoLogViewSet,
    ScheduledPostViewSet,
    VideoInventoryItemViewSet,
)

__all__ = [
    "AutoCutAnalysisViewSet",
    "AutoCutCorteViewSet",
    "AutoCutSuggestionViewSet",
    "BrandAssetViewSet",
    "BrandCategoryViewSet",
    "BrandSocialAccountViewSet",
    "BrandViewSet",
    "BrandYouTubeCredentialViewSet",
    "CutViewSet",
    "DashboardMetricsView",
    "FactoryPostingScheduleViewSet",
    "FactoryViewSet",
    "FactoryYoutubeDashboardView",
    "FactoryYoutubeVideosView",
    "JobViewSet",
    "MultipleCreatorViewSet",
    "PostedVideoLogViewSet",
    "RegisterViewSet",
    "ScheduledPostViewSet",
    "SearchChannelViewSet",
    "SourceVideoViewSet",
    "VideoInventoryItemViewSet",
]
