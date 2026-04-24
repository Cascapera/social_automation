"""Tests for title_humanizer: light title variation via emoji."""

from __future__ import annotations

import random

from django.test import SimpleTestCase

from apps.social.services.title_humanizer import humanize_title


class HumanizeTitleTests(SimpleTestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(humanize_title(""), "")
        self.assertEqual(humanize_title(None), "")

    def test_removes_emoji_when_many(self):
        rng = random.Random()
        rng.random = lambda: 0.0  # força trigger do primeiro branch
        rng.choice = lambda seq: seq[0]
        title = "Incrivel 🔥 Titulo 🚀 Bomba"
        out = humanize_title(title, rng=rng)
        self.assertTrue(out.count("🔥") + out.count("🚀") < 2)

    def test_prepends_emoji_when_no_emoji(self):
        rng = random.Random()
        # Sem emoji no titulo: o 1o branch (len>=2) faz short-circuit sem chamar random(),
        # entao random() e chamado apenas 1 vez no elif.
        rng.random = lambda: 0.0
        rng.choice = lambda seq: "🔥"
        out = humanize_title("Titulo sem emoji", rng=rng)
        self.assertTrue(out.startswith("🔥 "))

    def test_unchanged_when_probabilities_miss(self):
        rng = random.Random()
        rng.random = lambda: 0.99
        original = "Titulo 🎯 Normal"
        self.assertEqual(humanize_title(original, rng=rng), original)

    def test_does_not_prepend_when_already_starts_with_emoji(self):
        rng = random.Random()
        # Tem 1 emoji (len<2 short-circuit no 1o branch), random() chamado 1 vez no elif
        rng.random = lambda: 0.0
        rng.choice = lambda seq: "🔥"
        original = "🎯 Ja tem emoji"
        self.assertEqual(humanize_title(original, rng=rng), original)
