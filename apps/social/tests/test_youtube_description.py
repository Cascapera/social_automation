"""YouTube description assembly tests (pure function + mocks)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.social.services.youtube_description import (
    _build_related_links_block,
    build_youtube_description,
)


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

    def _make_long_corte(self, chapters, *, is_en=False):
        analysis = MagicMock()
        analysis.name = "Live X"
        analysis.source = None
        analysis.file = None
        analysis.convidados = "Fulano"
        analysis.prompt_version = "viral_en" if is_en else "viral_long"
        analysis.youtube_url = ""
        analysis.brand = None

        suggestion = MagicMock()
        suggestion.cut_type = "long"
        suggestion.raw_data = {"chapters": chapters}

        corte = MagicMock()
        corte.analysis = analysis
        corte.suggestion = suggestion
        return corte

    def test_chapters_appended_for_long_cut(self):
        chapters = [
            {"timestamp": "00:00", "title": "Intro"},
            {"timestamp": "02:15", "title": "Desenvolvimento"},
            {"timestamp": "08:30", "title": "Conclusão"},
        ]
        corte = self._make_long_corte(chapters)
        text = build_youtube_description(corte)
        self.assertIn("📍 Capítulos:", text)
        self.assertIn("00:00 Intro", text)
        self.assertIn("02:15 Desenvolvimento", text)
        self.assertIn("08:30 Conclusão", text)

    def test_chapters_english_header(self):
        chapters = [
            {"timestamp": "00:00", "title": "Intro"},
            {"timestamp": "02:15", "title": "Middle"},
            {"timestamp": "08:30", "title": "Outro"},
        ]
        corte = self._make_long_corte(chapters, is_en=True)
        text = build_youtube_description(corte)
        self.assertIn("📍 Chapters:", text)

    def test_chapters_combined_with_override(self):
        chapters = [
            {"timestamp": "00:00", "title": "Intro"},
            {"timestamp": "02:15", "title": "Middle"},
            {"timestamp": "08:30", "title": "Outro"},
        ]
        corte = self._make_long_corte(chapters)
        text = build_youtube_description(corte, description_override="Minha descrição custom")
        self.assertTrue(text.startswith("Minha descrição custom"))
        self.assertIn("📍 Capítulos:", text)

    def test_chapters_skipped_when_first_not_zero(self):
        chapters = [
            {"timestamp": "00:15", "title": "Intro"},
            {"timestamp": "02:15", "title": "Middle"},
            {"timestamp": "08:30", "title": "Outro"},
        ]
        corte = self._make_long_corte(chapters)
        text = build_youtube_description(corte)
        self.assertNotIn("📍", text)

    def test_chapters_skipped_when_fewer_than_three(self):
        chapters = [
            {"timestamp": "00:00", "title": "Intro"},
            {"timestamp": "02:15", "title": "Middle"},
        ]
        corte = self._make_long_corte(chapters)
        text = build_youtube_description(corte)
        self.assertNotIn("📍", text)

    def test_chapters_skipped_for_short_cut(self):
        chapters = [
            {"timestamp": "00:00", "title": "Intro"},
            {"timestamp": "00:15", "title": "Middle"},
            {"timestamp": "00:30", "title": "Outro"},
        ]
        corte = self._make_long_corte(chapters)
        corte.suggestion.cut_type = "short"
        text = build_youtube_description(corte)
        self.assertNotIn("📍", text)

    def test_hashtags_appended_from_tags(self):
        corte = self._make_long_corte([])
        corte.suggestion.raw_data = {
            "chapters": [],
            "tags": ["investimento", "renda fixa", "Selic", "tesouro direto", "cdi", "poupança"],
        }
        text = build_youtube_description(corte)
        self.assertIn("#investimento", text)
        self.assertIn("#rendafixa", text)
        self.assertIn("#selic", text)
        # máximo 5 hashtags
        self.assertLessEqual(text.count("#"), 5)

    def test_hashtags_skipped_when_fewer_than_three_tags(self):
        corte = self._make_long_corte([])
        corte.suggestion.raw_data = {"tags": ["só-uma"]}
        text = build_youtube_description(corte)
        self.assertNotIn("#", text)

    def test_hashtags_skipped_when_no_tags(self):
        corte = self._make_long_corte([])
        corte.suggestion.raw_data = {}
        text = build_youtube_description(corte)
        self.assertNotIn("#", text)

    def test_hashtags_combined_with_override(self):
        corte = self._make_long_corte([])
        corte.suggestion.raw_data = {"tags": ["finanças", "investimento", "cripto"]}
        text = build_youtube_description(corte, description_override="Minha descricao")
        self.assertTrue(text.startswith("Minha descricao"))
        self.assertIn("#financas", text)  # sem acento
        self.assertIn("#investimento", text)
        self.assertIn("#cripto", text)


class RelatedLinksBlockTests(SimpleTestCase):
    """Tests isolados para _build_related_links_block."""

    def test_returns_empty_when_no_brand(self):
        self.assertEqual(_build_related_links_block(None, None, False), "")

    def test_returns_empty_when_no_history(self):
        brand = MagicMock(id=1)
        with patch("apps.jobs.models.ScheduledPost") as mock_sp:
            mock_sp.objects.filter.return_value.exclude.return_value.order_by.return_value.values_list.return_value.__getitem__.return_value = []
            self.assertEqual(_build_related_links_block(brand, 123, False), "")

    def test_returns_links_when_history_present(self):
        brand = MagicMock(id=1)
        ext_history = [{"YTB": "abc111"}, {"YTB": "def222"}, {"YTB": "ghi333"}]
        with patch("apps.jobs.models.ScheduledPost") as mock_sp:
            mock_sp.objects.filter.return_value.exclude.return_value.order_by.return_value.values_list.return_value.__getitem__.return_value = ext_history
            out = _build_related_links_block(brand, 123, False)
        self.assertIn("▶️ Mais vídeos:", out)
        self.assertEqual(out.count("https://youtu.be/"), 2)

    def test_english_header(self):
        brand = MagicMock(id=1)
        ext_history = [{"YTB": "abc111"}, {"YTB": "def222"}]
        with patch("apps.jobs.models.ScheduledPost") as mock_sp:
            mock_sp.objects.filter.return_value.exclude.return_value.order_by.return_value.values_list.return_value.__getitem__.return_value = ext_history
            out = _build_related_links_block(brand, 123, True)
        self.assertIn("▶️ More videos:", out)
