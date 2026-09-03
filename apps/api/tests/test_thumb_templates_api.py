"""API dos modelos de capa: cadastro do modelo, padrão da marca e escolha no job."""

from __future__ import annotations

import io
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from apps.auto_cuts.models import AutoCutAnalysis
from apps.brands.models import Brand, BrandAsset, Factory

User = get_user_model()


def _png(size=(320, 180)):
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 216, 46)).save(buf, format="PNG")
    return SimpleUploadedFile("modelo.png", buf.getvalue(), content_type="image/png")


class ThumbTemplateApiTestCase(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media.name)
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.user = User.objects.create_user(username="capa", password="securepass1")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.factory = Factory.objects.create(name="F-api")
        self.brand = Brand.objects.create(name="B-api", slug="b-api", factory=self.factory)
        self.outra = Brand.objects.create(name="B-outra", slug="b-outra-api", factory=self.factory)

    def _asset(self, brand=None, asset_type="THUMB_LONG", label="modelo A", **kwargs):
        return BrandAsset.objects.create(
            brand=brand or self.brand,
            asset_type=asset_type,
            label=label,
            file=_png(),
            **kwargs,
        )


class CadastroDoModeloTests(ThumbTemplateApiTestCase):
    def test_cria_modelo_com_a_zona_padrao(self):
        res = self.client.post(
            "/api/brand-assets/",
            {
                "brand": self.brand.id,
                "asset_type": "THUMB_LONG",
                "label": "sala ao vivo",
                "file": _png(),
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["text_zone_x"], 60)
        self.assertEqual(res.data["text_align"], "center")
        self.assertFalse(res.data["stroke_enabled"])

    def test_dois_modelos_do_mesmo_tipo_convivem(self):
        for label in ("sala ao vivo", "entrevista"):
            res = self.client.post(
                "/api/brand-assets/",
                {
                    "brand": self.brand.id,
                    "asset_type": "THUMB_LONG",
                    "label": label,
                    "file": _png(),
                },
                format="multipart",
            )
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        self.assertEqual(
            BrandAsset.objects.filter(brand=self.brand, asset_type="THUMB_LONG").count(), 2
        )

    def test_modelo_sem_rotulo_e_recusado(self):
        """Sem rótulo, o segundo modelo colidiria no unique_together (brand, tipo, rótulo)."""
        res = self.client.post(
            "/api/brand-assets/",
            {"brand": self.brand.id, "asset_type": "THUMB_LONG", "file": _png()},
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("label", res.data)

    def test_zona_que_passa_da_borda_e_recusada(self):
        res = self.client.post(
            "/api/brand-assets/",
            {
                "brand": self.brand.id,
                "asset_type": "THUMB_LONG",
                "label": "torta",
                "file": _png(),
                "text_zone_x": 70,
                "text_zone_w": 40,
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("text_zone_w", res.data)

    def test_zona_com_largura_zero_e_recusada(self):
        res = self.client.post(
            "/api/brand-assets/",
            {
                "brand": self.brand.id,
                "asset_type": "THUMB_LONG",
                "label": "vazia",
                "file": _png(),
                "text_zone_w": 0,
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("text_zone_w", res.data)

    def test_zona_personalizada_e_gravada(self):
        res = self.client.post(
            "/api/brand-assets/",
            {
                "brand": self.brand.id,
                "asset_type": "THUMB_LONG",
                "label": "vazio embaixo",
                "file": _png(),
                "text_zone_x": 5,
                "text_zone_y": 70,
                "text_zone_w": 90,
                "text_zone_h": 25,
                "text_align": "left",
                "text_valign": "bottom",
                "text_color": "#101340",
                "stroke_enabled": True,
                "font": "anton",
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        asset = BrandAsset.objects.get(id=res.data["id"])
        self.assertEqual((asset.text_zone_x, asset.text_zone_h), (5, 25))
        self.assertEqual(asset.text_valign, "bottom")
        self.assertTrue(asset.stroke_enabled)

    def test_rotulo_nao_e_exigido_nos_outros_tipos_de_asset(self):
        res = self.client.post(
            "/api/brand-assets/",
            {"brand": self.brand.id, "asset_type": "LOGO", "file": _png()},
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)


class PadraoDaMarcaTests(ThumbTemplateApiTestCase):
    def test_define_o_modelo_padrao_da_marca(self):
        asset = self._asset()

        res = self.client.patch(
            f"/api/brands/{self.brand.id}/",
            {"default_thumb_template_long": asset.id},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.default_thumb_template_long_id, asset.id)

    def test_padrao_de_outra_marca_e_recusado(self):
        alheio = self._asset(brand=self.outra, label="da outra")

        res = self.client.patch(
            f"/api/brands/{self.brand.id}/",
            {"default_thumb_template_long": alheio.id},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("default_thumb_template_long", res.data)

    def test_padrao_com_o_formato_trocado_e_recusado(self):
        curto = self._asset(asset_type="THUMB_SHORT", label="curto")

        res = self.client.patch(
            f"/api/brands/{self.brand.id}/",
            {"default_thumb_template_long": curto.id},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


@patch("apps.api.views.auto_cuts.analyze_auto_cuts_task")
class EscolhaNoJobTests(ThumbTemplateApiTestCase):
    def _criar_job(self, **extra):
        payload = {
            "brand": self.brand.id,
            "name": "job de teste",
            "youtube_url": "https://youtu.be/abc123",
        }
        payload.update(extra)
        return self.client.post("/api/auto-cuts/", payload, format="multipart")

    def test_job_sem_escolha_fica_sem_modelo(self, _task):
        res = self._criar_job()

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        analysis = AutoCutAnalysis.objects.get(id=res.data["id"])
        self.assertIsNone(analysis.thumb_template_short_id)
        self.assertIsNone(analysis.thumb_template_long_id)

    def test_job_guarda_um_modelo_por_formato(self, _task):
        curto = self._asset(asset_type="THUMB_SHORT", label="curto")
        longo = self._asset(label="longo")

        res = self._criar_job(thumb_template_short=curto.id, thumb_template_long=longo.id)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        analysis = AutoCutAnalysis.objects.get(id=res.data["id"])
        self.assertEqual(analysis.thumb_template_short_id, curto.id)
        self.assertEqual(analysis.thumb_template_long_id, longo.id)

    def test_modelo_de_outra_marca_e_recusado(self, _task):
        alheio = self._asset(brand=self.outra, label="da outra")

        res = self._criar_job(thumb_template_long=alheio.id)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("inválido", res.data["error"])
        self.assertFalse(AutoCutAnalysis.objects.exists())

    def test_modelo_com_o_formato_trocado_e_recusado(self, _task):
        curto = self._asset(asset_type="THUMB_SHORT", label="curto")

        res = self._criar_job(thumb_template_long=curto.id)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("longs", res.data["error"])

    def test_modelo_inexistente_e_recusado(self, _task):
        res = self._criar_job(thumb_template_long=999999)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valor_vazio_significa_sem_modelo(self, _task):
        res = self._criar_job(thumb_template_long="")

        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        analysis = AutoCutAnalysis.objects.get(id=res.data["id"])
        self.assertIsNone(analysis.thumb_template_long_id)

    def test_serializer_devolve_os_modelos_escolhidos(self, _task):
        longo = self._asset(label="longo")

        res = self._criar_job(thumb_template_long=longo.id)

        self.assertEqual(res.data["thumb_template_long"], longo.id)
        self.assertIsNone(res.data["thumb_template_short"])


@patch("apps.api.views.auto_cuts.finalizar_auto_cut_task")
class TrocaNaFinalizacaoTests(ThumbTemplateApiTestCase):
    def setUp(self):
        super().setUp()
        self.analysis = AutoCutAnalysis.objects.create(
            user=self.user, brand=self.brand, name="job", status="done"
        )

    def test_finalizar_troca_o_modelo_do_job(self, _task):
        novo = self._asset(label="novo")

        res = self.client.post(
            f"/api/auto-cuts/{self.analysis.id}/finalizar/",
            {"thumb_template_long": novo.id},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.thumb_template_long_id, novo.id)

    def test_finalizar_sem_mencionar_o_modelo_nao_mexe_nele(self, _task):
        atual = self._asset(label="atual")
        self.analysis.thumb_template_long = atual
        self.analysis.save(update_fields=["thumb_template_long"])

        res = self.client.post(
            f"/api/auto-cuts/{self.analysis.id}/finalizar/", {}, format="json"
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.thumb_template_long_id, atual.id)

    def test_finalizar_com_modelo_vazio_remove_o_modelo(self, _task):
        atual = self._asset(label="atual")
        self.analysis.thumb_template_long = atual
        self.analysis.save(update_fields=["thumb_template_long"])

        res = self.client.post(
            f"/api/auto-cuts/{self.analysis.id}/finalizar/",
            {"thumb_template_long": ""},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.analysis.refresh_from_db()
        self.assertIsNone(self.analysis.thumb_template_long_id)

    def test_finalizar_com_modelo_de_outra_marca_e_recusado(self, _task):
        alheio = self._asset(brand=self.outra, label="da outra")

        res = self.client.post(
            f"/api/auto-cuts/{self.analysis.id}/finalizar/",
            {"thumb_template_long": alheio.id},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.analysis.refresh_from_db()
        self.assertIsNone(self.analysis.thumb_template_long_id)
