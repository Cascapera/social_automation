"""Registro de usuário.

Movido de `apps/api/serializers.py` no R-16 — movimentação pura.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["username", "email", "password"]
        extra_kwargs = {"email": {"required": False}}

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user
