"""REST API integration tests (registration, JWT, authenticated resources)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APIClient

from apps.brands.models import Brand, Factory
from apps.jobs.models import (
    FactoryPostingSchedule,
    PostedVideoLog,
    ScheduledPost,
    VideoInventoryItem,
)

User = get_user_model()


class ApiAuthAndBrandsTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_register_creates_user(self):
        res = self.client.post(
            "/api/register/",
            {"username": "newuser", "password": "securepass1", "email": "n@example.com"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["username"], "newuser")
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_obtain_token(self):
        User.objects.create_user(username="tokuser", password="securepass1")
        res = self.client.post(
            "/api/auth/token/",
            {"username": "tokuser", "password": "securepass1"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)

    def test_factories_list_requires_auth(self):
        res = self.client.get("/api/factories/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_factories_list_authenticated(self):
        user = User.objects.create_user(username="u1", password="securepass1")
        Factory.objects.create(name="F1")
        self.client.force_authenticate(user=user)
        res = self.client.get("/api/factories/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data), 1)

    def test_brands_list_authenticated(self):
        user = User.objects.create_user(username="u2", password="securepass1")
        factory = Factory.objects.create(name="F2")
        Brand.objects.create(name="Brand A", slug="brand-a", factory=factory)
        self.client.force_authenticate(user=user)
        res = self.client.get("/api/brands/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = [b["name"] for b in res.data]
        self.assertIn("Brand A", names)


class VideoInventoryMarkPostedTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="inventory-user", password="securepass1")
        self.client.force_authenticate(user=self.user)
        self.factory = Factory.objects.create(name="Factory Inventory")
        self.brand = Brand.objects.create(
            name="Brand Inventory",
            slug="brand-inventory",
            factory=self.factory,
        )

    def test_mark_posted_accepts_custom_posted_at(self):
        custom_posted_at = timezone.now().replace(second=0, microsecond=0)
        inventory = VideoInventoryItem.objects.create(
            factory=self.factory,
            brand=self.brand,
            video_type="SHORT",
            title="Video teste",
            status="SCHEDULED",
        )
        post = ScheduledPost.objects.create(
            platforms=["YT"],
            scheduled_at=custom_posted_at,
            title="Video teste",
            status="PENDING",
        )
        schedule = FactoryPostingSchedule.objects.create(
            factory=self.factory,
            brand=self.brand,
            inventory_item=inventory,
            video_type="SHORT",
            scheduled_at=custom_posted_at,
            status="PLANNED",
            next_retry_at=custom_posted_at,
            scheduled_post=post,
        )

        res = self.client.post(
            f"/api/video-inventory/{inventory.id}/mark-posted/",
            {"posted_at": custom_posted_at.isoformat()},
            format="json",
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        response_posted_at = res.data["posted_at"]
        if isinstance(response_posted_at, str):
            response_posted_at = parse_datetime(response_posted_at)
        self.assertEqual(response_posted_at, custom_posted_at)

        inventory.refresh_from_db()
        post.refresh_from_db()
        schedule.refresh_from_db()

        self.assertEqual(inventory.status, "POSTED")
        self.assertEqual(inventory.posted_at, custom_posted_at)
        self.assertEqual(post.status, "DONE")
        self.assertEqual(post.posted_at, custom_posted_at)
        self.assertEqual(schedule.status, "DONE")

        log = PostedVideoLog.objects.get(
            inventory_item=inventory,
            external_platform="MANUAL",
            external_video_id="manual",
        )
        self.assertEqual(log.posted_at, custom_posted_at)
        self.assertTrue(log.metadata_snapshot["manual_post"])
        self.assertEqual(log.metadata_snapshot["manual_posted_at"], custom_posted_at.isoformat())


class VideoInventoryBucketFilterTests(TestCase):
    """Paginação independente por bucket (awaiting / posted) no /api/video-inventory/."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="bucket-user", password="securepass1")
        self.client.force_authenticate(user=self.user)
        self.factory = Factory.objects.create(name="Factory Bucket")
        self.brand = Brand.objects.create(
            name="Brand Bucket",
            slug="brand-bucket",
            factory=self.factory,
        )

        def make(status, video_type="SHORT", title=""):
            return VideoInventoryItem.objects.create(
                factory=self.factory,
                brand=self.brand,
                video_type=video_type,
                title=title or f"{status}-{video_type}",
                status=status,
            )

        # 3 awaiting (status variados) + 2 posted, com tipos misturados.
        self.awaiting_available = make("AVAILABLE")
        self.awaiting_scheduled = make("SCHEDULED")
        self.awaiting_failed = make("FAILED", video_type="LONG")
        self.posted_a = make("POSTED")
        self.posted_b = make("POSTED", video_type="LONG")

    def test_bucket_awaiting_excludes_posted(self):
        res = self.client.get("/api/video-inventory/", {"bucket": "awaiting"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        statuses = {row["status"] for row in res.data["results"]}
        self.assertEqual(res.data["count"], 3)
        self.assertEqual(statuses, {"AVAILABLE", "SCHEDULED", "FAILED"})

    def test_bucket_posted_only_posted(self):
        res = self.client.get("/api/video-inventory/", {"bucket": "posted"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        statuses = {row["status"] for row in res.data["results"]}
        self.assertEqual(res.data["count"], 2)
        self.assertEqual(statuses, {"POSTED"})

    def test_no_bucket_keeps_legacy_behavior(self):
        """Sem bucket: comportamento antigo (todos juntos), garantindo retrocompatibilidade."""
        res = self.client.get("/api/video-inventory/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 5)

    def test_unknown_bucket_keeps_legacy_behavior(self):
        res = self.client.get("/api/video-inventory/", {"bucket": "wat"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 5)

    def test_bucket_awaiting_respects_page_size(self):
        # page_size=2 sobre 3 itens awaiting → 2 páginas
        res = self.client.get(
            "/api/video-inventory/",
            {"bucket": "awaiting", "page_size": 2, "page": 1},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 3)
        self.assertEqual(len(res.data["results"]), 2)

        res2 = self.client.get(
            "/api/video-inventory/",
            {"bucket": "awaiting", "page_size": 2, "page": 2},
        )
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res2.data["results"]), 1)

    def test_bucket_combined_with_video_type_filter(self):
        res = self.client.get(
            "/api/video-inventory/",
            {"bucket": "awaiting", "video_type": "LONG"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)
        self.assertEqual(res.data["results"][0]["id"], self.awaiting_failed.id)

    def test_bucket_combined_with_factory_filter(self):
        other_factory = Factory.objects.create(name="Factory Bucket Other")
        other_brand = Brand.objects.create(
            name="Brand Bucket Other",
            slug="brand-bucket-other",
            factory=other_factory,
        )
        VideoInventoryItem.objects.create(
            factory=other_factory,
            brand=other_brand,
            video_type="SHORT",
            title="other",
            status="POSTED",
        )

        res = self.client.get(
            "/api/video-inventory/",
            {"bucket": "posted", "factory": self.factory.id},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)

    def test_buckets_are_disjoint_and_sum_to_total(self):
        awaiting = self.client.get("/api/video-inventory/", {"bucket": "awaiting"}).data["count"]
        posted = self.client.get("/api/video-inventory/", {"bucket": "posted"}).data["count"]
        total = self.client.get("/api/video-inventory/").data["count"]
        self.assertEqual(awaiting + posted, total)
