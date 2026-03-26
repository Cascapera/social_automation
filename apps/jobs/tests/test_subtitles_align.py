"""Subtitle alignment pure-function tests."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.jobs.services.subtitles import align_edited_to_original_words


class AlignEditedWordsTests(SimpleTestCase):
    def test_empty_original_returns_none(self):
        self.assertIsNone(align_edited_to_original_words("a b", []))

    def test_empty_edited_returns_none(self):
        words = [{"start": 0.0, "end": 1.0, "word": "x"}]
        self.assertIsNone(align_edited_to_original_words("   ", words))

    def test_one_to_one_mapping(self):
        ow = [
            {"start": 0.0, "end": 0.5, "word": "a"},
            {"start": 0.5, "end": 1.0, "word": "b"},
        ]
        out = align_edited_to_original_words("hello world", ow)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["word"], "hello")
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[1]["end"], 1.0)

    def test_more_tokens_splits_time(self):
        ow = [{"start": 0.0, "end": 10.0, "word": "a"}]
        out = align_edited_to_original_words("one two three", ow)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[-1]["end"], 10.0)

    def test_fewer_tokens_merges_timestamps(self):
        ow = [
            {"start": 0.0, "end": 1.0, "word": "a"},
            {"start": 1.0, "end": 2.0, "word": "b"},
            {"start": 2.0, "end": 3.0, "word": "c"},
        ]
        out = align_edited_to_original_words("merged", ow)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 3.0)
