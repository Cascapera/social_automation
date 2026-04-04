"""Interface base para publishers."""
from abc import ABC, abstractmethod

from apps.brands.models import BrandSocialAccount


class BasePublisher(ABC):
    @abstractmethod
    def publish(
        self,
        account: BrandSocialAccount,
        video_path: str,
        job=None,
        scheduled_post=None,
    ) -> dict:
        """Publica o vídeo na plataforma. Retorna dados da publicação."""
        pass
