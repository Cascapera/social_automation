"""YouTube credential helpers (token refresh)."""
import os
from datetime import UTC

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from apps.brands.models import BrandSocialAccount, BrandYouTubeCredential
from apps.social.services.secret_crypto import decrypt_secret


def get_credentials(
    account: BrandSocialAccount,
    youtube_credential: BrandYouTubeCredential | None = None,
    use_check_client: bool = False,
) -> Credentials:
    """Return valid Credentials, refreshing access_token if expired."""
    brand = getattr(account, "brand", None)
    source = youtube_credential if youtube_credential is not None else brand
    source_secret = ""
    source_client_id = ""
    if source is not None:
        try:
            source_secret = decrypt_secret(
                getattr(source, "client_secret", "")
                or getattr(source, "youtube_client_secret", "")
            )
        except ValueError as exc:
            raise ValueError(
                "Failed to decrypt YouTube client_secret. "
                "Check SOCIAL_ENCRYPTION_KEY."
            ) from exc
        source_client_id = str(
            getattr(source, "client_id", "")
            or getattr(source, "youtube_client_id", "")
            or ""
        ).strip()
    check_client_id = (os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()
    check_client_secret = (os.getenv("YOUTUBE_CHECK_CLIENT_SECRET") or "").strip()
    if use_check_client and check_client_id and check_client_secret:
        client_id = check_client_id
        client_secret = check_client_secret
    else:
        client_id = source_client_id or os.getenv("GOOGLE_CLIENT_ID")
        client_secret = source_secret or os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError(
            "OAuth do YouTube não configurado para a brand "
            "(youtube_client_id/youtube_client_secret) e sem fallback global."
        )
    token_source = youtube_credential if youtube_credential is not None else account
    expiry = token_source.expires_at
    if expiry and getattr(expiry, "tzinfo", None) is None:
        expiry = expiry.replace(tzinfo=UTC)
    creds = Credentials(
        token=token_source.access_token or None,
        refresh_token=token_source.refresh_token or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
        expiry=expiry,
    )
    # Without refresh_token we cannot renew automatically.
    if not creds.refresh_token:
        raise ValueError("Conta YouTube sem refresh_token. Reconecte a conta no OAuth.")
    # Refresh when expired or when current token is missing.
    if creds.expired or not creds.token:
        creds.refresh(Request())
        token_source.access_token = creds.token
        if creds.expiry:
            token_source.expires_at = creds.expiry
        token_source.save(update_fields=["access_token", "expires_at", "updated_at"])
    return creds
