"""
Configuração global do pytest (carregada antes do Django).
Garante variáveis mínimas para CI e desenvolvimento sem .env.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_DEBUG", "1")
# Criptografia de segredos em testes (roundtrip encrypt/decrypt)
os.environ.setdefault("SOCIAL_ENCRYPTION_KEY", "test-encryption-key-only-for-pytest")
