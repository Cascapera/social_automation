"""Serializer tests (validation and computed fields)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.api.serializers import (
    FactorySerializer,
    UserRegisterSerializer,
    VideoInventoryItemSerializer,
)
from apps.brands.models import Brand, Factory
from apps.jobs.models import FactoryPostingSchedule, ScheduledPost, VideoInventoryItem

User = get_user_model()


class UserRegisterSerializerTests(TestCase):
    def test_create_user(self):
        s = UserRegisterSerializer(
            data={"username": "reg1", "password": "longenough", "email": "a@b.com"}
        )
        self.assertTrue(s.is_valid(), s.errors)
        user = s.save()
        self.assertEqual(user.username, "reg1")
        self.assertTrue(user.check_password("longenough"))


class FactorySerializerTests(TestCase):
    def test_serialize_factory(self):
        f = Factory.objects.create(name="SF")
        data = FactorySerializer(f).data
        self.assertEqual(data["name"], "SF")
        self.assertIn("has_youtube_check_credential", data)


class VideoInventoryItemSerializerTests(TestCase):
    def test_exposes_latest_scheduled_post_id(self):
        factory = Factory.objects.create(name="Factory Test")
        brand = Brand.objects.create(name="Brand Test", slug="brand-test", factory=factory)
        item = VideoInventoryItem.objects.create(
            factory=factory,
            brand=brand,
            video_type="LONG",
            status="SCHEDULED",
            title="Video para cruzar com log",
        )
        older_post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(),
            platforms=["YTB"],
        )
        latest_post = ScheduledPost.objects.create(
            scheduled_at=timezone.now(),
            platforms=["YTB"],
        )
        FactoryPostingSchedule.objects.create(
            factory=factory,
            brand=brand,
            inventory_item=item,
            video_type="LONG",
            scheduled_at=timezone.now(),
            scheduled_post=older_post,
        )
        FactoryPostingSchedule.objects.create(
            factory=factory,
            brand=brand,
            inventory_item=item,
            video_type="LONG",
            scheduled_at=timezone.now(),
            scheduled_post=latest_post,
        )

        serialized = VideoInventoryItemSerializer(item).data

        self.assertEqual(serialized["scheduled_post_id"], latest_post.id)
