"""Testes de modelos de brands."""

from __future__ import annotations

from django.test import TestCase

from apps.brands.models import Brand, Factory


class FactoryBrandModelTests(TestCase):
    def test_factory_str(self):
        f = Factory.objects.create(name="Minha Factory")
        self.assertEqual(str(f), "Minha Factory")

    def test_brand_str(self):
        factory = Factory.objects.create(name="F")
        b = Brand.objects.create(name="Canal Um", slug="canal-um", factory=factory)
        self.assertIn("Canal Um", str(b))
