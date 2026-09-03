"""Escolha do modelo de capa no job de cortes."""

from __future__ import annotations

from django.test import TestCase

from apps.auto_cuts.models import AutoCutAnalysis
from apps.brands.models import Brand, Factory
from apps.brands.tests.test_thumb_templates import make_thumb


class AutoCutThumbTemplateTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="F-job")
        self.brand = Brand.objects.create(name="B-job", slug="b-job", factory=self.factory)

    def test_job_sem_escolha_fica_nulo(self):
        analysis = AutoCutAnalysis.objects.create(brand=self.brand, name="sem modelo")

        self.assertIsNone(analysis.thumb_template_short_id)
        self.assertIsNone(analysis.thumb_template_long_id)

    def test_job_guarda_um_modelo_por_formato(self):
        short = make_thumb(self.brand, "short A", asset_type="THUMB_SHORT")
        long_ = make_thumb(self.brand, "long A")

        analysis = AutoCutAnalysis.objects.create(
            brand=self.brand,
            name="com modelo",
            thumb_template_short=short,
            thumb_template_long=long_,
        )
        analysis.refresh_from_db()

        self.assertEqual(analysis.thumb_template_short_id, short.id)
        self.assertEqual(analysis.thumb_template_long_id, long_.id)

    def test_apagar_modelo_com_job_em_andamento_nao_derruba_o_job(self):
        long_ = make_thumb(self.brand, "long A")
        analysis = AutoCutAnalysis.objects.create(
            brand=self.brand, name="em andamento", thumb_template_long=long_
        )

        long_.delete()
        analysis.refresh_from_db()

        self.assertIsNone(analysis.thumb_template_long_id)
        self.assertEqual(analysis.name, "em andamento")
