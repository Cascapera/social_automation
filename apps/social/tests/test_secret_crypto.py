"""Secret encryption tests (Fernet)."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.social.services.secret_crypto import ENCRYPTED_PREFIX, decrypt_secret, encrypt_secret


class SecretCryptoTests(SimpleTestCase):
    def test_roundtrip(self):
        raw = "meu-segredo-api"
        enc = encrypt_secret(raw)
        self.assertTrue(enc.startswith(ENCRYPTED_PREFIX))
        self.assertEqual(decrypt_secret(enc), raw)

    def test_encrypt_empty_returns_empty(self):
        self.assertEqual(encrypt_secret(""), "")
        self.assertEqual(encrypt_secret("   "), "")

    def test_decrypt_plain_legacy(self):
        self.assertEqual(decrypt_secret("texto-antigo-sem-prefixo"), "texto-antigo-sem-prefixo")

    def test_double_encrypt_no_double_wrap(self):
        raw = "x"
        once = encrypt_secret(raw)
        twice = encrypt_secret(once)
        self.assertEqual(twice, once)
