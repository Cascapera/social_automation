"""Publishers por plataforma."""
from apps.social.publishers.base import BasePublisher


def get_publisher(platform: str) -> BasePublisher | None:
    if platform in ("YT", "YTB"):
        from apps.social.publishers.youtube import YouTubePublisher

        return YouTubePublisher()
    return None
