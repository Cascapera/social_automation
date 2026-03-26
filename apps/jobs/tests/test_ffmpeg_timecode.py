"""Testes de utilitários FFmpeg (timecode)."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.jobs.services.ffmpeg import tc_to_seconds


class TcToSecondsTests(SimpleTestCase):
    def test_empty(self):
        self.assertEqual(tc_to_seconds(""), 0.0)

    def test_seconds_only(self):
        self.assertEqual(tc_to_seconds("45"), 45.0)

    def test_mm_ss(self):
        self.assertEqual(tc_to_seconds("01:30"), 90.0)

    def test_hh_mm_ss(self):
        self.assertEqual(tc_to_seconds("01:00:00"), 3600.0)
