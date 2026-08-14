"""Serializers da API, por domínio (refactor.md R-16 / D-04).

Era um `serializers.py` de 1.047 linhas com 24 alterações por ano. Mesma técnica do R-15,
validada nele primeiro: quebra por domínio, **sem editar uma linha de corpo de método**, e
este `__init__` reexporta os nomes públicos para que ninguém precise mudar o import.
"""

from .auth import UserRegisterSerializer
from .auto_cuts import (
    AutoCutAnalysisSerializer,
    AutoCutCorteSerializer,
    AutoCutReadyChunkSerializer,
    AutoCutSuggestionSerializer,
)
from .brands import (
    BrandAssetSerializer,
    BrandCategorySerializer,
    BrandSerializer,
    BrandSocialAccountSerializer,
    BrandYouTubeCredentialSerializer,
)
from .factories import FactorySerializer, SearchChannelSerializer
from .jobs import JobCutInlineSerializer, JobRunSerializer, JobSerializer
from .media import CutBulkCreateSerializer, CutSerializer, SourceVideoSerializer
from .multiple_creator import (
    MultipleCreatorBrandExecutionSerializer,
    MultipleCreatorJobSerializer,
)
from .posting import (
    FactoryPostingScheduleSerializer,
    PostedVideoLogSerializer,
    ScheduledPostSerializer,
    VideoInventoryItemSerializer,
)

__all__ = [
    "AutoCutAnalysisSerializer",
    "AutoCutCorteSerializer",
    "AutoCutReadyChunkSerializer",
    "AutoCutSuggestionSerializer",
    "BrandAssetSerializer",
    "BrandCategorySerializer",
    "BrandSerializer",
    "BrandSocialAccountSerializer",
    "BrandYouTubeCredentialSerializer",
    "CutBulkCreateSerializer",
    "CutSerializer",
    "FactoryPostingScheduleSerializer",
    "FactorySerializer",
    "JobCutInlineSerializer",
    "JobRunSerializer",
    "JobSerializer",
    "MultipleCreatorBrandExecutionSerializer",
    "MultipleCreatorJobSerializer",
    "PostedVideoLogSerializer",
    "ScheduledPostSerializer",
    "SearchChannelSerializer",
    "SourceVideoSerializer",
    "UserRegisterSerializer",
    "VideoInventoryItemSerializer",
]
