"""Mediahub model tests."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.brands.models import Brand, Factory
from apps.mediahub.models import SourceVideo

User = get_user_model()


class SourceVideoModelTests(TestCase):
    def test_source_str(self):
        factory = Factory.objects.create(name="FM")
        brand = Brand.objects.create(name="BM", slug="bm", factory=factory)
        user = User.objects.create_user(username="sv", password="securepass1")
        s = SourceVideo.objects.create(
            brand=brand,
            user=user,
            title="Vídeo origem",
            file=SimpleUploadedFile("in.mp4", b"x", content_type="video/mp4"),
        )
        self.assertIn("bm", str(s))
        self.assertIn("Vídeo origem", str(s))
