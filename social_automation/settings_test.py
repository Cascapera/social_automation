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
