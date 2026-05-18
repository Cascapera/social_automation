"""
Settings for pytest/CI: in-memory SQLite (ignores DATABASE_URL from .env or host).
"""

from __future__ import annotations

import os

# Before loading settings: avoid short SECRET_KEY in shell (JWT / SimpleJWT).
if len(os.environ.get("DJANGO_SECRET_KEY", "")) < 32:
    os.environ["DJANGO_SECRET_KEY"] = "test-secret-key-only-for-pytest-and-ci-min-32-chars"

# Import full configuration and override only the database.
from social_automation.settings import *  # noqa: F403, F405

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Testes sempre com cache em memória, independente de DJANGO_DEBUG no ambiente.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "OPTIONS": {"MAX_ENTRIES": 100},
    }
}

# Broker in-memory: tasks .delay() chamadas em views nao tentam Redis em CI.
# Tasks NAO sao executadas (ALWAYS_EAGER=False); quem quiser execucao sincrona
# patcheia/override_settings no proprio teste.
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
