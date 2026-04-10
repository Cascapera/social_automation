from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.brands.models import Brand, Factory
from apps.jobs.models import FactoryPostingSchedule, Job, ScheduledPost, VideoInventoryItem
from apps.social.tasks import check_scheduled_posts_task, generate_daily_factory_schedules_task

User = get_user_model()


class DailyScheduleWindowTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(
            name="Factory Janela",
            timezone="America/Sao_Paulo",
        )
        self.brand = Brand.objects.create(
            name="Brand Janela",
            slug="brand-janela",
            factory=self.factory,
        )

    @patch("apps.social.tasks.generate_daily_schedule_for_factory")
    @patch("apps.social.tasks.timezone.now")
    def test_generate_task_creates_same_day_schedule_in_morning_window(
        self,
        mock_now: MagicMock,
        mock_generate: MagicMock,
    ):
        mock_now.return_value = datetime(2026, 4, 10, 12, 5, tzinfo=UTC)  # 09:05 BRT
        mock_generate.return_value = {"created": 2, "run_id": 42}

        result = generate_daily_factory_schedules_task()

        mock_generate.assert_called_once_with(
            self.factory,
            now_utc=mock_now.return_value,
            target_date=date(2026, 4, 10),
            allow_rerun=True,
            correlation_id=ANY,
        )
        self.assertEqual(result["generated_factories"], 1)
        self.assertEqual(result["created_posts"], 2)

    @patch("apps.social.tasks.generate_daily_schedule_for_factory")
    @patch("apps.social.tasks.timezone.now")
    def test_generate_task_skips_after_last_catchup_window(
        self,
        mock_now: MagicMock,
        mock_generate: MagicMock,
    ):
        mock_now.return_value = datetime(2026, 4, 10, 17, 5, tzinfo=UTC)  # 14:05 BRT

        result = generate_daily_factory_schedules_task()

        mock_generate.assert_not_called()
        self.assertEqual(result["generated_factories"], 0)
        self.assertEqual(result["created_posts"], 0)

    @patch("apps.social.tasks.generate_daily_schedule_for_factory")
    @patch("apps.social.tasks.timezone.now")
    def test_generate_task_skips_when_day_schedule_already_exists(
        self,
        mock_now: MagicMock,
        mock_generate: MagicMock,
    ):
        now_utc = datetime(2026, 4, 10, 14, 5, tzinfo=UTC)  # 11:05 BRT
        mock_now.return_value = now_utc
        item = VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            video_type="LONG",
            status="SCHEDULED",
        )
        FactoryPostingSchedule.objects.create(
            factory=self.factory,
            brand=self.brand,
            inventory_item=item,
            video_type="LONG",
            scheduled_at=now_utc + timedelta(hours=3),
            status="PLANNED",
        )

        result = generate_daily_factory_schedules_task()

        mock_generate.assert_not_called()
        self.assertEqual(result["generated_factories"], 0)
        self.assertEqual(result["created_posts"], 0)


class UploadDispatchWindowTests(TestCase):
    def setUp(self):
        self.factory = Factory.objects.create(
            name="Factory Dispatch",
            timezone="America/Sao_Paulo",
        )
        self.brand = Brand.objects.create(
            name="Brand Dispatch",
            slug="brand-dispatch",
            factory=self.factory,
        )
        self.user = User.objects.create_user(username="dispatch-user", password="securepass1")
        self.job = Job.objects.create(user=self.user, brand=self.brand, name="Job Dispatch")

    def _post(self, **kwargs) -> ScheduledPost:
        defaults = dict(
            job=self.job,
            platforms=["YTB"],
            scheduled_at=timezone.now(),
            title="Video teste",
            status="PENDING",
        )
        defaults.update(kwargs)
        return ScheduledPost.objects.create(**defaults)

    @patch("apps.social.tasks.process_brand_posting_queue_task.apply_async")
    @patch("apps.social.tasks.timezone.now")
    def test_check_scheduled_posts_queues_only_youtube_posts_within_one_hour_window(
        self,
        mock_now: MagicMock,
        mock_apply_async: MagicMock,
    ):
        now = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
        mock_now.return_value = now
        within_window = self._post(scheduled_at=now + timedelta(minutes=45))
        self._post(scheduled_at=now + timedelta(hours=2))

        result = check_scheduled_posts_task()

        mock_apply_async.assert_called_once_with(
            args=[self.brand.id, [within_window.id]],
            countdown=0,
        )
        self.assertEqual(result["queued"], 1)
