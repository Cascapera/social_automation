"""REST API integration tests (registration, JWT, authenticated resources)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.brands.models import Brand, Factory

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
