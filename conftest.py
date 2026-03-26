"""
Global pytest configuration (loaded before Django).
Ensures minimum environment variables for CI and development without .env.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_DEBUG", "1")
# Secret encryption in tests (encrypt/decrypt roundtrip)
os.environ.setdefault("SOCIAL_ENCRYPTION_KEY", "test-encryption-key-only-for-pytest")
