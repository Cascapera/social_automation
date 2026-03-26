"""Testes básicos de modelos Auto Cuts."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.auto_cuts.models import AutoCutAnalysis
from apps.brands.models import Brand, Factory

User = get_user_model()


class AutoCutAnalysisModelTests(TestCase):
    def test_create_minimal(self):
        factory = Factory.objects.create(name="FA2")
        brand = Brand.objects.create(name="B2", slug="b2", factory=factory)
        user = User.objects.create_user(username="ac", password="securepass1")
        a = AutoCutAnalysis.objects.create(
            user=user,
            brand=brand,
            name="Análise teste",
            status="pending",
        )
        self.assertEqual(a.status, "pending")
        self.assertIn("Análise teste", str(a))
