"""Criptografia simples para segredos sensíveis em banco."""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTED_PREFIX = "enc:v1:"
ENV_KEY_NAME = "SOCIAL_ENCRYPTION_KEY"


def _build_fernet() -> Fernet:
    key_material = (os.getenv(ENV_KEY_NAME) or "").strip()
    if not key_material:
        raise ValueError(
            f"{ENV_KEY_NAME} não configurada. Defina uma chave forte no .env."
        )
    digest = hashlib.sha256(key_material.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt_secret(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(ENCRYPTED_PREFIX):
        return raw
    token = _build_fernet().encrypt(raw.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.startswith(ENCRYPTED_PREFIX):
        # Compatibilidade com dados antigos em texto puro.
        return raw
    token = raw[len(ENCRYPTED_PREFIX):]
    try:
        return _build_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Segredo criptografado inválido para a chave atual.") from exc


def is_secret_configured(value: str) -> bool:
    return bool(str(value or "").strip())
