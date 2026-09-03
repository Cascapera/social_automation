"""Modelos de capa: vários por marca, zona de texto e padrão da marca."""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.brands.models import Brand, BrandAsset, Factory

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_thumb(brand, label, asset_type="THUMB_LONG", **kwargs):
    return BrandAsset.objects.create(
        brand=brand,
        asset_type=asset_type,
        label=label,
        file=SimpleUploadedFile(f"{label or 'modelo'}.png", PNG_1PX, content_type="image/png"),
        **kwargs,
    )


class ThumbTemplateModelTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="F-capa")
        self.brand = Brand.objects.create(name="B-capa", slug="b-capa", factory=self.factory)

    def test_varios_modelos_por_marca_com_rotulos_diferentes(self):
        """O ponto da feature: mais de um modelo do mesmo tipo convive na mesma marca."""
        make_thumb(self.brand, "sala ao vivo")
        make_thumb(self.brand, "entrevista")

        assets = BrandAsset.objects.filter(brand=self.brand, asset_type="THUMB_LONG")
        self.assertEqual(assets.count(), 2)
        self.assertEqual(
            sorted(assets.values_list("label", flat=True)), ["entrevista", "sala ao vivo"]
        )

    def test_rotulo_repetido_no_mesmo_tipo_continua_bloqueado(self):
        make_thumb(self.brand, "sala ao vivo")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_thumb(self.brand, "sala ao vivo")

    def test_zona_de_texto_tem_padrao_na_lateral_direita(self):
        """Padrão medido em docs/cortes_sala_modelo.png: modelo daquele formato não precisa de ajuste."""
        asset = make_thumb(self.brand, "padrao")

        self.assertEqual(
            (asset.text_zone_x, asset.text_zone_y, asset.text_zone_w, asset.text_zone_h),
            (60, 8, 38, 84),
        )
        self.assertEqual(asset.text_align, "center")
        self.assertEqual(asset.text_valign, "middle")
        self.assertFalse(asset.stroke_enabled, "contorno sai desligado: a arte do modelo já dá contraste")
        self.assertEqual(asset.font, "")
        self.assertEqual(asset.text_color, "")

    def test_zona_de_texto_aceita_valores_proprios(self):
        asset = make_thumb(
            self.brand,
            "vazio embaixo",
            text_zone_x=5,
            text_zone_y=70,
            text_zone_w=90,
            text_zone_h=25,
            text_align="left",
            text_valign="bottom",
            text_color="#101340",
            font="anton",
            stroke_enabled=True,
            stroke_color="#FFFFFF",
        )
        asset.refresh_from_db()

        self.assertEqual((asset.text_zone_x, asset.text_zone_h), (5, 25))
        self.assertEqual(asset.text_align, "left")
        self.assertEqual(asset.text_valign, "bottom")
        self.assertEqual(asset.text_color, "#101340")
        self.assertTrue(asset.stroke_enabled)

    def test_padrao_da_marca_por_formato(self):
        short = make_thumb(self.brand, "short A", asset_type="THUMB_SHORT")
        long_ = make_thumb(self.brand, "long A")

        self.brand.default_thumb_template_short = short
        self.brand.default_thumb_template_long = long_
        self.brand.save(update_fields=["default_thumb_template_short", "default_thumb_template_long"])
        self.brand.refresh_from_db()

        self.assertEqual(self.brand.default_thumb_template_short_id, short.id)
        self.assertEqual(self.brand.default_thumb_template_long_id, long_.id)

    def test_apagar_o_modelo_padrao_apenas_limpa_o_ponteiro(self):
        """SET_NULL: a marca sobrevive e a geração cai no fallback, em vez de estourar."""
        long_ = make_thumb(self.brand, "long A")
        self.brand.default_thumb_template_long = long_
        self.brand.save(update_fields=["default_thumb_template_long"])

        long_.delete()
        self.brand.refresh_from_db()

        self.assertIsNone(self.brand.default_thumb_template_long_id)
        self.assertTrue(Brand.objects.filter(pk=self.brand.pk).exists())
