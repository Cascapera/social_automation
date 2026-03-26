"""Serializer tests (validation and computed fields)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.api.serializers import FactorySerializer, UserRegisterSerializer
from apps.brands.models import Factory

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
