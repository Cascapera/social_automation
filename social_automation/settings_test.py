"""
Settings para pytest/CI: SQLite em memória (ignora DATABASE_URL do .env ou do host).
"""

from __future__ import annotations

import os

# Antes de carregar settings: evita SECRET_KEY curta no shell (JWT / SimpleJWT).
if len(os.environ.get("DJANGO_SECRET_KEY", "")) < 32:
    os.environ["DJANGO_SECRET_KEY"] = "test-secret-key-only-for-pytest-and-ci-min-32-chars"

# Importa configuração completa e sobrescreve apenas o banco.
from social_automation.settings import *  # noqa: F403, F405

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
