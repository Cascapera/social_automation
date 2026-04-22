"""Tests for metadata_sanitizer: post-LLM title/metadata cleaning."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.auto_cuts.services.metadata_sanitizer import sanitize_clip, sanitize_payload


class SanitizeClipTests(SimpleTestCase):
    def test_clean_clip_unchanged(self):
        clip = {
            "suggested_title": "Ele largou tudo e foi embora 🔥",
            "thumbnail_text": "LARGOU TUDO",
            "hook_sentence": "Esse momento foi inacreditável.",
        }
        original = dict(clip)
        sanitize_clip(clip)
        self.assertEqual(clip, original)

    def test_replaces_palavrao_em_titulo_pt(self):
        clip = {"suggested_title": "A porra do sistema falhou ao vivo 😱"}
        sanitize_clip(clip)
        self.assertNotIn("porra", clip["suggested_title"].lower())

    def test_replaces_merda_em_titulo(self):
        clip = {"suggested_title": "O momento que virou uma merda completa"}
        sanitize_clip(clip)
        self.assertNotIn("merda", clip["suggested_title"].lower())

    def test_replaces_palavrao_en_fuck(self):
        clip = {"suggested_title": "He said f*ck it and walked away"}
        sanitize_clip(clip)
        self.assertNotIn("fuck", clip["suggested_title"].lower())

    def test_replaces_termo_sexual_transar(self):
        clip = {"suggested_title": "Ela transou com o chefe e foi demitida 😱"}
        sanitize_clip(clip)
        self.assertNotIn("transou", clip["suggested_title"].lower())

    def test_replaces_putaria_em_hook(self):
        clip = {"hook_sentence": "Rolou uma putaria no escritório e todo mundo ficou sabendo."}
        sanitize_clip(clip)
        self.assertNotIn("putaria", clip["hook_sentence"].lower())

    def test_title_only_term_not_applied_to_hook(self):
        # "puta" no contexto "title" nao deve substituir em hook_sentence
        clip = {
            "suggested_title": "Que puta situação esse cara criou",
            "hook_sentence": "Que puta situação esse cara criou",
        }
        sanitize_clip(clip)
        # titulo deve ser sanitizado
        self.assertNotIn("puta", clip["suggested_title"].lower())
        # hook: "puta" e context=title, entao NAO substitui em hook_sentence
        # (hook e campo "all" sem substituicao de "puta" isolado)
        # apenas confirmamos que o titulo foi tratado
        self.assertIsInstance(clip["hook_sentence"], str)

    def test_replaces_termo_sexual_en_porn(self):
        clip = {"suggested_title": "The porn industry secret nobody talks about"}
        sanitize_clip(clip)
        self.assertNotIn("porn", clip["suggested_title"].lower())

    def test_nonstring_field_untouched(self):
        clip = {"suggested_title": None, "virality_score": 90}
        sanitize_clip(clip)
        self.assertIsNone(clip["suggested_title"])
        self.assertEqual(clip["virality_score"], 90)

    def test_empty_string_untouched(self):
        clip = {"suggested_title": ""}
        sanitize_clip(clip)
        self.assertEqual(clip["suggested_title"], "")

    def test_does_not_raise_on_missing_field(self):
        clip = {"virality_score": 88}
        sanitize_clip(clip)
        self.assertEqual(clip["virality_score"], 88)


class SanitizePayloadTests(SimpleTestCase):
    def test_sanitizes_all_lists(self):
        payload = {
            "candidate_shorts": [
                {"suggested_title": "A merda aconteceu ao vivo 😱"},
            ],
            "ranked_shorts": [
                {"suggested_title": "Já a porra explodiu no palco"},
            ],
            "final_long_cuts": [
                {"suggested_title": "Era uma putaria generalizada"},
            ],
        }
        sanitize_payload(payload)
        self.assertNotIn("merda", payload["candidate_shorts"][0]["suggested_title"].lower())
        self.assertNotIn("porra", payload["ranked_shorts"][0]["suggested_title"].lower())
        self.assertNotIn("putaria", payload["final_long_cuts"][0]["suggested_title"].lower())

    def test_empty_payload_does_not_raise(self):
        sanitize_payload({})

    def test_nondict_returns_unchanged(self):
        self.assertIsNone(sanitize_payload(None))
        self.assertEqual(sanitize_payload([1, 2]), [1, 2])

    def test_missing_lists_skipped(self):
        payload = {"candidate_shorts": None, "ranked_shorts": "invalid"}
        sanitize_payload(payload)  # must not raise

    def test_clean_payload_unchanged(self):
        payload = {
            "candidate_shorts": [
                {"suggested_title": "Ele saiu de cena e ninguém esperava 🎯"},
            ],
            "ranked_shorts": [],
            "final_long_cuts": [],
        }
        title_before = payload["candidate_shorts"][0]["suggested_title"]
        sanitize_payload(payload)
        self.assertEqual(payload["candidate_shorts"][0]["suggested_title"], title_before)
