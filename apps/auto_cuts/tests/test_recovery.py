from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.auto_cuts.models import AutoCutAnalysis, AutoCutCorte, AutoCutSuggestion
from apps.auto_cuts.services.recovery import (
    MANUAL_ATTENTION_PREFIX,
    AutoCutRecoveryAction,
    recover_autocut_analysis,
    recover_stuck_autocut_analyses,
)
from apps.brands.models import Brand, Factory
from apps.jobs.models import VideoInventoryItem


class AutoCutRecoveryTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.factory = Factory.objects.create(name="Factory Recovery")
        self.brand = Brand.objects.create(
            name="Brand Recovery",
            slug="brand-recovery",
            factory=self.factory,
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _create_analysis(self, **overrides) -> AutoCutAnalysis:
        defaults = {
            "brand": self.brand,
            "name": "Recovery Analysis",
            "status": "pending",
        }
        defaults.update(overrides)
        analysis = AutoCutAnalysis.objects.create(**defaults)
        analysis.file.save(
            f"analysis_{analysis.id}.mp4",
            SimpleUploadedFile("analysis.mp4", b"analysis-video", content_type="video/mp4"),
            save=True,
        )
        return analysis

    def _create_suggestion(self, analysis: AutoCutAnalysis, **overrides) -> AutoCutSuggestion:
        defaults = {
            "analysis": analysis,
            "cut_type": "short",
            "start_tc": "00:00",
            "end_tc": "00:30",
            "title": "Recovered Cut",
            "source_asset_id": f"analysis:{analysis.id}",
        }
        defaults.update(overrides)
        return AutoCutSuggestion.objects.create(**defaults)

    def _create_corte(self, analysis: AutoCutAnalysis, **overrides) -> AutoCutCorte:
        suggestion = overrides.pop("suggestion", None) or self._create_suggestion(analysis)
        file_name = overrides.pop("file_name", "cut.mp4")
        defaults = {
            "analysis": analysis,
            "suggestion": suggestion,
            "format": "vertical",
            "needs_subtitle": True,
            "user_wants_finalize": True,
            "is_finalized": False,
            "subtitle_segments": [{"start": 0.0, "end": 1.0, "text": "oi"}],
        }
        defaults.update(overrides)
        corte = AutoCutCorte.objects.create(**defaults)
        corte.file.save(
            file_name,
            SimpleUploadedFile(file_name, b"cut-video", content_type="video/mp4"),
            save=True,
        )
        return corte

    def _create_inventory_item(self, corte: AutoCutCorte, status: str = "AVAILABLE") -> VideoInventoryItem:
        suggestion = corte.suggestion
        return VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            auto_cut_corte=corte,
            video_type="SHORT" if suggestion.cut_type == "short" else "LONG",
            title=suggestion.title,
            source_asset_id=suggestion.source_asset_id,
            source_metadata={"analysis_id": corte.analysis_id, "suggestion_id": suggestion.id},
            status=status,
            last_error="",
        )

    def _set_stale(self, analysis: AutoCutAnalysis, *, minutes: int = 180) -> None:
        AutoCutAnalysis.objects.filter(id=analysis.id).update(
            updated_at=timezone.now() - timedelta(minutes=minutes)
        )
        analysis.refresh_from_db()

    def test_interrupted_early_stage_restarts_from_beginning(self):
        analysis = self._create_analysis(status="transcribing")
        analysis.transcript = "partial transcript"
        analysis.transcript_segments = [{"start": 0.0, "end": 1.0, "text": "partial"}]
        analysis.save(update_fields=["transcript", "transcript_segments"])
        suggestion = self._create_suggestion(analysis)
        corte = self._create_corte(analysis, suggestion=suggestion, file_name="partial_cut.mp4")
        corte.thumbnail.save(
            "thumb.jpg",
            SimpleUploadedFile("thumb.jpg", b"thumb", content_type="image/jpeg"),
            save=True,
        )
        self._create_inventory_item(corte, status="FAILED")
        self._set_stale(analysis)

        with patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay") as delay_mock:
            result = recover_autocut_analysis(analysis)

        analysis.refresh_from_db()
        self.assertEqual(result.action, AutoCutRecoveryAction.RESTART_FROM_BEGINNING.value)
        self.assertEqual(analysis.status, "pending")
        self.assertEqual(analysis.transcript, "")
        self.assertIsNone(analysis.transcript_segments)
        self.assertFalse(AutoCutSuggestion.objects.filter(id=suggestion.id).exists())
        self.assertFalse(AutoCutCorte.objects.filter(id=corte.id).exists())
        self.assertEqual(VideoInventoryItem.objects.count(), 0)
        delay_mock.assert_called_once_with(analysis.id)

    def test_interrupted_finalization_restores_media_and_reruns_finalization(self):
        analysis = self._create_analysis(status="finalizing")
        suggestion = self._create_suggestion(analysis)
        corte = self._create_corte(analysis, suggestion=suggestion, file_name="partial_final.mp4")
        inventory = self._create_inventory_item(corte, status="AVAILABLE")
        self._set_stale(analysis)

        def fake_extract(video_path, start_tc, end_tc, output_path, use_gpu=False):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"restored-base-cut")
            return output_path

        with (
            patch("apps.auto_cuts.services.recovery.extract_corte", side_effect=fake_extract),
            patch("apps.auto_cuts.tasks.finalizar_auto_cut_task.apply_async") as apply_mock,
        ):
            result = recover_autocut_analysis(analysis)

        analysis.refresh_from_db()
        corte.refresh_from_db()
        self.assertEqual(result.action, AutoCutRecoveryAction.RERUN_FINALIZATION.value)
        self.assertEqual(analysis.status, "finalizing")
        self.assertFalse(corte.is_finalized)
        self.assertTrue(Path(corte.file.path).exists())
        self.assertTrue(VideoInventoryItem.objects.filter(id=inventory.id).exists())
        apply_mock.assert_called_once()

    def test_healthy_completed_analysis_is_ignored(self):
        analysis = self._create_analysis(status="done")
        suggestion = self._create_suggestion(analysis)
        corte = self._create_corte(
            analysis,
            suggestion=suggestion,
            file_name="healthy_cut.mp4",
            is_finalized=True,
        )
        self._create_inventory_item(corte, status="AVAILABLE")

        with (
            patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay") as delay_mock,
            patch("apps.auto_cuts.tasks.finalizar_auto_cut_task.apply_async") as apply_mock,
        ):
            result = recover_autocut_analysis(analysis, force=True)

        analysis.refresh_from_db()
        self.assertEqual(result.action, AutoCutRecoveryAction.IGNORE.value)
        self.assertEqual(analysis.status, "done")
        delay_mock.assert_not_called()
        apply_mock.assert_not_called()

    def test_done_but_not_finalized_state_is_corrected_via_finalization_recovery(self):
        analysis = self._create_analysis(status="done")
        suggestion = self._create_suggestion(analysis)
        self._create_corte(analysis, suggestion=suggestion, file_name="not_finalized.mp4")
        self._set_stale(analysis)

        def fake_extract(video_path, start_tc, end_tc, output_path, use_gpu=False):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"restored-from-source")
            return output_path

        with (
            patch("apps.auto_cuts.services.recovery.extract_corte", side_effect=fake_extract),
            patch("apps.auto_cuts.tasks.finalizar_auto_cut_task.apply_async") as apply_mock,
        ):
            result = recover_autocut_analysis(analysis)

        analysis.refresh_from_db()
        self.assertEqual(result.action, AutoCutRecoveryAction.RERUN_FINALIZATION.value)
        self.assertEqual(analysis.status, "finalizing")
        apply_mock.assert_called_once()

    def test_repeated_recovery_does_not_duplicate_requeue(self):
        analysis = self._create_analysis(status="transcribing")
        self._set_stale(analysis)

        with patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay") as delay_mock:
            first_result = recover_autocut_analysis(analysis)
            analysis.refresh_from_db()
            second_result = recover_autocut_analysis(analysis)

        self.assertEqual(first_result.action, AutoCutRecoveryAction.RESTART_FROM_BEGINNING.value)
        self.assertEqual(second_result.action, AutoCutRecoveryAction.IGNORE.value)
        self.assertIn("Cooldown", second_result.reason)
        self.assertEqual(delay_mock.call_count, 1)

    def test_targeted_recovery_respects_cooldown_without_force(self):
        analysis = self._create_analysis(status="transcribing")

        with patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay") as delay_mock:
            results = recover_stuck_autocut_analyses(analysis_id=analysis.id)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, AutoCutRecoveryAction.IGNORE.value)
        self.assertIn("Cooldown", results[0].reason)
        delay_mock.assert_not_called()

    def test_ambiguous_state_is_marked_for_manual_attention(self):
        analysis = self._create_analysis(status="finalizing")
        suggestion = self._create_suggestion(analysis)
        corte = self._create_corte(analysis, suggestion=suggestion, file_name="scheduled_cut.mp4")
        self._create_inventory_item(corte, status="SCHEDULED")
        self._set_stale(analysis)

        with (
            patch("apps.auto_cuts.tasks.analyze_auto_cuts_task.delay") as delay_mock,
            patch("apps.auto_cuts.tasks.finalizar_auto_cut_task.apply_async") as apply_mock,
        ):
            result = recover_autocut_analysis(analysis)

        analysis.refresh_from_db()
        self.assertEqual(result.action, AutoCutRecoveryAction.MARK_MANUAL_ATTENTION.value)
        self.assertEqual(analysis.status, "error")
        self.assertIn(MANUAL_ATTENTION_PREFIX, analysis.error)
        delay_mock.assert_not_called()
        apply_mock.assert_not_called()

    def test_finalized_media_without_inventory_only_resyncs_inventory(self):
        analysis = self._create_analysis(status="finalizing")
        suggestion = self._create_suggestion(analysis)
        corte = self._create_corte(
            analysis,
            suggestion=suggestion,
            file_name="finalized_no_inventory.mp4",
            is_finalized=True,
        )
        self._set_stale(analysis)

        result = recover_autocut_analysis(analysis)

        analysis.refresh_from_db()
        self.assertEqual(result.action, AutoCutRecoveryAction.RERUN_INVENTORY_SYNC.value)
        self.assertEqual(analysis.status, "done")
        self.assertTrue(
            VideoInventoryItem.objects.filter(
                auto_cut_corte=corte,
                status="AVAILABLE",
            ).exists()
        )
