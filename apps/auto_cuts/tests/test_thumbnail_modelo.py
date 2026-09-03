"""Capa com modelo: texto na zona do modelo; sem modelo, faixa inferior (comportamento antigo)."""

from __future__ import annotations

import io
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.auto_cuts.services import thumbnail as thumb_mod
from apps.auto_cuts.services.thumbnail import (
    _draw_text_in_zone,
    _resolve_text_zone,
    _resolve_thumb_template,
    generate_auto_thumbnail,
)
from apps.brands.models import Brand, BrandAsset, Factory

FRAME_COLOR = (20, 120, 200)
BAND_COLOR = (225, 46, 32)
TEMPLATE_BG = (255, 216, 46)
INK_COLOR = (16, 19, 64)

TITULO = "classificação das regiões"


def _png_bytes(size, color):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _template_asset(brand, label, asset_type="THUMB_LONG", size=(320, 180), **kwargs):
    """Modelo opaco, como docs/cortes_sala_modelo.png: arte de ponta a ponta."""
    return BrandAsset.objects.create(
        brand=brand,
        asset_type=asset_type,
        label=label,
        file=SimpleUploadedFile(
            f"{label}.png", _png_bytes(size, TEMPLATE_BG), content_type="image/png"
        ),
        **kwargs,
    )


def _fake_frame(video_path, output_image_path, primary_sec, fallback_sec):
    """Substitui o FFmpeg: grava um frame liso, para o teste não depender de vídeo real."""
    Image.new("RGB", (320, 180), FRAME_COLOR).save(output_image_path, format="PNG")


def _close(color, reference, tolerance=24):
    """JPEG não devolve a cor exata; compara com folga."""
    return all(abs(a - b) <= tolerance for a, b in zip(color, reference, strict=True))


class DesenhoNaZonaTests(TestCase):
    """A garantia central da feature: a tinta fica dentro da zona, e só lá."""

    def setUp(self):
        self.img = Image.new("RGB", (1280, 720), TEMPLATE_BG)
        self.draw = ImageDraw.Draw(self.img)

    def _draw(self, text=TITULO, zone=(768, 57, 1254, 661), **kwargs):
        options = {
            "preferred_font": "impact",
            "text_color": INK_COLOR,
            "stroke_color": (255, 255, 255),
            "stroke_enabled": False,
            "align": "center",
            "valign": "middle",
        }
        options.update(kwargs)
        _draw_text_in_zone(self.draw, text, zone, **options)
        return zone

    def _ink_pixels(self, region=None):
        px = self.img.load()
        w, h = self.img.size
        x1, y1, x2, y2 = region or (0, 0, w, h)
        return [
            (x, y)
            for y in range(y1, y2)
            for x in range(x1, x2)
            if px[x, y] != TEMPLATE_BG
        ]

    def _ink_outside(self, zone):
        x1, y1, x2, y2 = zone
        return [
            (x, y)
            for (x, y) in self._ink_pixels()
            if not (x1 <= x < x2 and y1 <= y < y2)
        ]

    def test_escreve_dentro_da_zona(self):
        zone = self._draw()
        self.assertTrue(self._ink_pixels(zone), "devia haver texto desenhado na zona")

    def test_nao_sobra_tinta_fora_da_zona(self):
        """O bug que a feature corrige: antes o texto ia sempre para a faixa inferior."""
        zone = self._draw()
        self.assertEqual(self._ink_outside(zone), [])

    def test_titulo_enorme_encolhe_e_continua_dentro(self):
        zone = self._draw(text=" ".join([TITULO] * 15))
        self.assertTrue(self._ink_pixels(zone))
        self.assertEqual(self._ink_outside(zone), [])

    def test_palavra_unica_gigante_continua_dentro(self):
        zone = self._draw(text="pneumoultramicroscopicossilicovulcanoconiotico")
        self.assertTrue(self._ink_pixels(zone))
        self.assertEqual(self._ink_outside(zone), [])

    def test_com_contorno_a_tinta_tambem_fica_dentro(self):
        zone = self._draw(stroke_enabled=True)
        self.assertTrue(self._ink_pixels(zone))
        self.assertEqual(self._ink_outside(zone), [])

    def test_zona_estreita_no_rodape_continua_contida(self):
        zone = self._draw(zone=(16, 560, 1264, 700), align="left", valign="bottom")
        self.assertTrue(self._ink_pixels(zone))
        self.assertEqual(self._ink_outside(zone), [])

    def test_alinhamento_a_direita_encosta_no_lado_direito(self):
        zone = (768, 57, 1254, 661)
        self._draw(text="curto", zone=zone, align="right")
        xs = [x for (x, _y) in self._ink_pixels(zone)]
        # a tinta termina perto da borda direita e não começa perto da esquerda
        self.assertGreater(max(xs), zone[2] - 40)
        self.assertGreater(min(xs), zone[0] + 40)

    def test_alinhamento_a_esquerda_encosta_no_lado_esquerdo(self):
        zone = (768, 57, 1254, 661)
        self._draw(text="curto", zone=zone, align="left")
        xs = [x for (x, _y) in self._ink_pixels(zone)]
        self.assertLess(min(xs), zone[0] + 40)

    def test_valign_topo_e_base_movem_o_bloco(self):
        zone = (768, 57, 1254, 661)
        self._draw(text="curto", zone=zone, valign="top")
        topo = [y for (_x, y) in self._ink_pixels(zone)]
        self.setUp()
        self._draw(text="curto", zone=zone, valign="bottom")
        base = [y for (_x, y) in self._ink_pixels(zone)]

        self.assertLess(min(topo), zone[1] + 40)
        self.assertGreater(max(base), zone[3] - 40)
        self.assertLess(max(topo), min(base))


class ThumbnailComModeloTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media.name)
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.factory = Factory.objects.create(name="F-thumb")
        self.brand = Brand.objects.create(name="B-thumb", slug="b-thumb", factory=self.factory)
        self.analysis = AutoCutAnalysis.objects.create(
            brand=self.brand,
            name="job",
            # O frame é falsificado em _fake_frame; o ficheiro só precisa existir no disco.
            file=SimpleUploadedFile("fonte.mp4", b"nao-e-um-mp4-de-verdade"),
        )
        self.suggestion = AutoCutSuggestion.objects.create(
            analysis=self.analysis,
            cut_type="long",
            start_tc="00:00",
            end_tc="00:30",
            title=TITULO,
            raw_data={"suggested_title": TITULO},
        )

    def _generate(self, fmt="horizontal"):
        corte = AutoCutCorte.objects.create(
            analysis=self.analysis, suggestion=self.suggestion, format=fmt
        )
        with patch.object(thumb_mod, "_extract_frame_with_fallback", _fake_frame):
            ok = generate_auto_thumbnail(corte)
        self.assertTrue(ok, "a geração da capa devia ter sucesso")
        corte.refresh_from_db()
        return Image.open(corte.thumbnail.path).convert("RGB")

    def test_sem_modelo_mantem_a_faixa_inferior(self):
        """Comportamento de antes da feature, intacto."""
        img = self._generate()
        w, h = img.size

        self.assertTrue(_close(img.getpixel((4, h - 4)), BAND_COLOR), "faixa nos 20% de baixo")
        self.assertTrue(_close(img.getpixel((4, 4)), FRAME_COLOR), "acima da faixa, o frame")

    def test_com_modelo_a_faixa_inferior_desaparece(self):
        _template_asset(self.brand, "modelo A")
        img = self._generate()
        w, h = img.size

        self.assertFalse(
            _close(img.getpixel((4, h - 4)), BAND_COLOR), "com modelo não resta faixa vermelha"
        )
        self.assertTrue(_close(img.getpixel((4, 4)), TEMPLATE_BG), "o modelo opaco cobre o frame")

    def test_com_modelo_o_texto_vai_para_a_direita(self):
        _template_asset(self.brand, "modelo A")
        img = self._generate()
        w, h = img.size
        px = img.load()

        def tem_tinta(x1, x2):
            return any(
                not _close(px[x, y], TEMPLATE_BG)
                for y in range(h)
                for x in range(x1, x2)
            )

        self.assertTrue(tem_tinta(int(w * 0.6), w), "texto na lateral direita")
        self.assertFalse(tem_tinta(0, int(w * 0.5)), "nada escrito na metade esquerda")

    def test_modelo_de_shorts_e_usado_no_corte_vertical(self):
        _template_asset(self.brand, "short A", asset_type="THUMB_SHORT", size=(180, 320))
        img = self._generate(fmt="vertical")
        w, h = img.size

        self.assertLess(w, h, "short sai em 9:16")
        self.assertTrue(_close(img.getpixel((4, 4)), TEMPLATE_BG))


class ResolucaoDoModeloTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(name="F-res")
        self.brand = Brand.objects.create(name="B-res", slug="b-res", factory=self.factory)
        self.outra = Brand.objects.create(name="B-outra", slug="b-outra", factory=self.factory)
        self.analysis = AutoCutAnalysis.objects.create(brand=self.brand, name="job")

    def test_sem_asset_nenhum_devolve_none(self):
        self.assertIsNone(_resolve_thumb_template(self.analysis, self.brand, is_short=False))

    def test_sem_marca_devolve_none(self):
        self.assertIsNone(_resolve_thumb_template(self.analysis, None, is_short=False))

    def test_escolha_do_job_ganha_do_padrao_da_marca(self):
        escolhido = _template_asset(self.brand, "escolhido")
        padrao = _template_asset(self.brand, "padrao")
        self.brand.default_thumb_template_long = padrao
        self.brand.save(update_fields=["default_thumb_template_long"])
        self.analysis.thumb_template_long = escolhido
        self.analysis.save(update_fields=["thumb_template_long"])

        self.assertEqual(
            _resolve_thumb_template(self.analysis, self.brand, is_short=False).id, escolhido.id
        )

    def test_padrao_da_marca_quando_o_job_nao_escolheu(self):
        _template_asset(self.brand, "primeiro")
        padrao = _template_asset(self.brand, "padrao")
        self.brand.default_thumb_template_long = padrao
        self.brand.save(update_fields=["default_thumb_template_long"])

        self.assertEqual(
            _resolve_thumb_template(self.analysis, self.brand, is_short=False).id, padrao.id
        )

    def test_marca_sem_padrao_configurado_usa_o_primeiro(self):
        """Regressão: marca que já usava modelo antes da feature não pode perder a capa."""
        primeiro = _template_asset(self.brand, "primeiro")
        _template_asset(self.brand, "segundo")

        self.assertEqual(
            _resolve_thumb_template(self.analysis, self.brand, is_short=False).id, primeiro.id
        )

    def test_modelo_de_outra_marca_e_ignorado(self):
        """Roteamento por tema: o corte da marca B não pode sair com a arte da marca A."""
        alheio = _template_asset(self.outra, "da outra marca")
        self.analysis.thumb_template_long = alheio
        self.analysis.save(update_fields=["thumb_template_long"])
        proprio = _template_asset(self.brand, "meu")

        self.assertEqual(
            _resolve_thumb_template(self.analysis, self.brand, is_short=False).id, proprio.id
        )

    def test_modelo_do_formato_errado_e_ignorado(self):
        curto = _template_asset(self.brand, "curto", asset_type="THUMB_SHORT")
        self.analysis.thumb_template_long = curto
        self.analysis.save(update_fields=["thumb_template_long"])

        self.assertIsNone(_resolve_thumb_template(self.analysis, self.brand, is_short=False))

    def test_short_e_long_sao_resolvidos_de_forma_independente(self):
        curto = _template_asset(self.brand, "curto", asset_type="THUMB_SHORT")
        longo = _template_asset(self.brand, "longo")

        self.assertEqual(_resolve_thumb_template(self.analysis, self.brand, True).id, curto.id)
        self.assertEqual(_resolve_thumb_template(self.analysis, self.brand, False).id, longo.id)


class ZonaDeTextoTests(TestCase):
    class _Modelo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def test_padrao_e_a_lateral_direita(self):
        zona = _resolve_text_zone(
            1280, 720, self._Modelo(text_zone_x=60, text_zone_y=8, text_zone_w=38, text_zone_h=84)
        )
        self.assertEqual(zona, (768, 57, 1254, 661))

    def test_zona_nunca_passa_da_borda(self):
        zona = _resolve_text_zone(
            1000, 500, self._Modelo(text_zone_x=80, text_zone_y=80, text_zone_w=90, text_zone_h=90)
        )
        self.assertEqual(zona, (800, 400, 1000, 500))

    def test_zona_degenerada_cai_na_imagem_inteira(self):
        """Zona mal configurada não pode fazer o texto sumir."""
        zona = _resolve_text_zone(
            1000, 500, self._Modelo(text_zone_x=99, text_zone_y=0, text_zone_w=1, text_zone_h=100)
        )
        self.assertEqual(zona, (0, 0, 1000, 500))

    def test_percentagem_invalida_usa_o_padrao(self):
        zona = _resolve_text_zone(
            1000,
            1000,
            self._Modelo(text_zone_x=None, text_zone_y="", text_zone_w=None, text_zone_h=None),
        )
        self.assertEqual(zona, (600, 80, 980, 920))
