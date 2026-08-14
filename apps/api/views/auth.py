"""Registro de usuário.

Movido de `apps/api/views.py` no R-15 — movimentação pura.
"""


from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..serializers import (
    UserRegisterSerializer,
)


class RegisterViewSet(viewsets.ViewSet):
    """Registro de novo usuário."""
    permission_classes = [AllowAny]
    serializer_class = UserRegisterSerializer

    def create(self, request):
        serializer = UserRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {"id": user.id, "username": user.username, "email": getattr(user, "email", "")},
            status=status.HTTP_201_CREATED,
        )
