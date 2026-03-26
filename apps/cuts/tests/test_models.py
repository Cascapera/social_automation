"""Testes do app cuts."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.brands.models import Brand, Factory
from apps.cuts.models import Cut
from apps.mediahub.models import SourceVideo

User = get_user_model()


class CutModelTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="FC")
        self.brand = Brand.objects.create(name="BC", slug="bc", factory=self.factory)
        self.user = User.objects.create_user(username="cu", password="securepass1")
        self.source = SourceVideo.objects.create(
            brand=self.brand,
            user=self.user,
            title="Src",
            file=SimpleUploadedFile("s.mp4", b"x", content_type="video/mp4"),
        )

    def test_cut_str(self):
        cut = Cut.objects.create(
            source=self.source,
            brand=self.brand,
            user=self.user,
            name="Corte 1",
            start_tc="00:00:01",
            end_tc="00:00:05",
            format="vertical",
            duration=4.0,
        )
        self.assertIn("00:00:01", str(cut))
        self.assertIn("00:00:05", str(cut))
