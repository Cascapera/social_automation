from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from django.apps import apps
from django.db import models
from django.test import TestCase, override_settings

from apps.social.tasks import _cleanup_orphan_media_files

SWEEP_FOLDERS = [
    "auto_cuts/sources",
    "auto_cuts/cortes",
    "auto_cuts/thumbnails",
    "sources",
    "exports",
    "cuts",
    "brands/assets",
]


class OrphanSweepInvariantTests(TestCase):
    """Canary: every FileField/ImageField whose upload_to lands in a swept
    folder MUST be reachable via _get_referenced_media_paths. Otherwise the
    sweep will silently delete live files."""

    def test_every_swept_filefield_is_collected_by_refs(self):
        from apps.auto_cuts.models import (
            AutoCutAnalysis,
            AutoCutCorte,
            AutoCutReadyChunk,
        )
        from apps.brands.models import BrandAsset
        from apps.cuts.models import Cut
        from apps.jobs.models import RenderOutput
        from apps.mediahub.models import SourceVideo

        # Static expectation — updated intentionally when adding a new field.
        # (model, field_name, upload_to_prefix)
        expected = [
            (SourceVideo, "file", "sources/"),
            (AutoCutAnalysis, "file", "auto_cuts/sources/"),
            (AutoCutReadyChunk, "file", "auto_cuts/ready_chunks/"),
            (AutoCutCorte, "file", "auto_cuts/cortes/"),
            (AutoCutCorte, "thumbnail", "auto_cuts/thumbnails/"),
            (BrandAsset, "file", "brands/assets/"),
            (RenderOutput, "file", "exports/"),
            (Cut, "file", "cuts/"),
        ]

        discovered = []
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, (models.FileField, models.ImageField)):
                    upload_to = getattr(field, "upload_to", "") or ""
                    if isinstance(upload_to, str):
                        discovered.append((model, field.name, upload_to))

        # Every FileField/ImageField whose upload_to starts with any swept
        # folder MUST appear in the static expected list. If this fails,
        # either (a) add the new field to _get_referenced_media_paths AND to
        # the expected list, or (b) move the field out of swept folders.
        for model, fname, upload_to in discovered:
            swept = any(
                upload_to.rstrip("/").startswith(folder)
                for folder in SWEEP_FOLDERS
            )
            if not swept:
                continue
            self.assertIn(
                (model, fname, upload_to),
                expected,
                f"{model.__name__}.{fname} ({upload_to}) is under a swept "
                f"folder but not in the canary list — update "
                f"_get_referenced_media_paths and this test.",
            )


class OrphanSweepMtimeGuardTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        os.makedirs(os.path.join(self.media_root, "exports"), exist_ok=True)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _write(self, rel_path: str, mtime_offset_seconds: float) -> Path:
        p = Path(self.media_root) / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        ts = time.time() + mtime_offset_seconds
        os.utime(p, (ts, ts))
        return p

    def test_recent_orphan_is_skipped(self):
        recent = self._write("exports/fresh.mp4", -60)
        result = _cleanup_orphan_media_files(min_age_hours=24)
        self.assertTrue(recent.exists())
        self.assertEqual(result["orphans_deleted"], 0)
        self.assertGreaterEqual(result["skipped_recent"], 1)

    def test_old_orphan_is_deleted(self):
        old = self._write("exports/stale.mp4", -(48 * 3600))
        result = _cleanup_orphan_media_files(min_age_hours=24)
        self.assertFalse(old.exists())
        self.assertEqual(result["orphans_deleted"], 1)

    def test_dry_run_does_not_delete_old_orphan(self):
        old = self._write("exports/stale.mp4", -(48 * 3600))
        result = _cleanup_orphan_media_files(dry_run=True, min_age_hours=24)
        self.assertTrue(old.exists())
        self.assertEqual(result["orphans_deleted"], 0)
        self.assertEqual(len(result["orphans_found"]), 1)
