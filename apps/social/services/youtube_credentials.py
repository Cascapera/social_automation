"""Helpers para credenciais YouTube (refresh de token)."""
import os
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from apps.brands.models import BrandSocialAccount


def get_credentials(account: BrandSocialAccount) -> Credentials:
    """Retorna Credentials válidas, renovando access_token se expirado."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET devem estar configurados")
    expiry = account.expires_at
    if expiry and getattr(expiry, "tzinfo", None) is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    creds = Credentials(
        token=account.access_token or None,
        refresh_token=account.refresh_token or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
        expiry=expiry,
    )
    # Sem refresh_token não há como renovar automaticamente.
    if not creds.refresh_token:
        raise ValueError("Conta YouTube sem refresh_token. Reconecte a conta no OAuth.")
    # Renova quando expirado ou quando não temos token atual.
    if creds.expired or not creds.token:
        creds.refresh(Request())
        account.access_token = creds.token
        if creds.expiry:
            account.expires_at = creds.expiry
        account.save(update_fields=["access_token", "expires_at"])
    return creds
