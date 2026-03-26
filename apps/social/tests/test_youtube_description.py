"""Testes da montagem de descrição YouTube (função pura + mocks)."""

from __future__ import annotations

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from apps.social.services.youtube_description import build_youtube_description


class BuildYoutubeDescriptionTests(SimpleTestCase):
    def test_override_wins(self):
        corte = MagicMock()
        self.assertEqual(
            build_youtube_description(corte, description_override="  texto fixo  "),
            "texto fixo",
        )

    def test_no_corte_no_title_returns_empty(self):
        self.assertEqual(build_youtube_description(None, title=None), "")

    def test_title_only_without_analysis(self):
        corte = MagicMock()
        corte.analysis = None
        self.assertEqual(build_youtube_description(corte, title="  T  "), "T")

    def test_portuguese_block_with_youtube_url_and_brand_extra(self):
        analysis = MagicMock()
        analysis.name = "Live X"
        analysis.source = None
        analysis.file = None
        analysis.convidados = "Fulano"
        analysis.prompt_version = "viral"
        analysis.youtube_url = "https://youtu.be/abc"
        analysis.brand = None

        brand = MagicMock()
        brand.youtube_description_extra = "Extra linha"

        corte = MagicMock()
        corte.analysis = analysis

        text = build_youtube_description(corte, brand=brand)
        self.assertIn("Corte da live", text)
        self.assertIn("Live X", text)
        self.assertIn("https://youtu.be/abc", text)
        self.assertIn("Extra linha", text)

    def test_english_prompt_suffix(self):
        analysis = MagicMock()
        analysis.name = "Show"
        analysis.source = None
        analysis.file = None
        analysis.convidados = "Guest"
        analysis.prompt_version = "viral_en"
        analysis.youtube_url = ""
        analysis.brand = None

        corte = MagicMock()
        corte.analysis = analysis
        text = build_youtube_description(corte)
        self.assertIn("Clip from live", text)
        self.assertIn("Guest", text)
