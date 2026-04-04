"""Serviço OAuth para YouTube."""
import os
from urllib.parse import urlencode

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from apps.social.services.secret_crypto import decrypt_secret

# State prefix para OAuth da factory (canais de busca)
FACTORY_CHECK_STATE_PREFIX = "fcheck:"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def get_client_config(brand=None, youtube_credential=None):
    """Retorna client_config da credencial, brand, ou fallback no .env."""
    secret_source = youtube_credential if youtube_credential is not None else brand
    source_client_id = ""
    source_redirect_uri = ""
    source_secret = ""
    if secret_source is not None:
        try:
            source_secret = decrypt_secret(
                getattr(secret_source, "client_secret", "")
                or getattr(secret_source, "youtube_client_secret", "")
            )
        except ValueError as exc:
            raise ValueError(
                "Não foi possível ler client_secret do YouTube. "
                "Verifique SOCIAL_ENCRYPTION_KEY no backend."
            ) from exc
        source_client_id = str(
            getattr(secret_source, "client_id", "")
            or getattr(secret_source, "youtube_client_id", "")
            or ""
        ).strip()
        source_redirect_uri = str(
            getattr(secret_source, "redirect_uri", "")
            or getattr(secret_source, "youtube_redirect_uri", "")
            or ""
        ).strip()
    client_id = source_client_id or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = source_secret or os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    redirect_uri = source_redirect_uri or os.getenv(
        "YOUTUBE_REDIRECT_URI",
        "http://localhost:8000/api/youtube/callback/",
    )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def get_redirect_uri(brand=None, youtube_credential=None):
    source = youtube_credential if youtube_credential is not None else brand
    return (
        str(
            getattr(source, "redirect_uri", "")
            or getattr(source, "youtube_redirect_uri", "")
            or ""
        ).strip()
        if source is not None else ""
    ) or os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/callback/")


def build_state_value(brand_id: int, youtube_credential_id: int | None = None) -> str:
    if youtube_credential_id:
        return f"{int(brand_id)}:{int(youtube_credential_id)}"
    return str(int(brand_id))


def parse_state_value(state: str) -> tuple[int, int | None]:
    raw = str(state or "").strip()
    if not raw:
        raise ValueError("state vazio")
    if ":" not in raw:
        return int(raw), None
    brand_part, cred_part = raw.split(":", 1)
    brand_id = int(brand_part)
    cred_id = int(cred_part) if cred_part.strip() else None
    return brand_id, cred_id


def get_authorization_url(brand_id: int, youtube_credential_id: int | None = None) -> str:
    """Retorna URL para o usuário autorizar no Google. state=brand_id.

    Montamos a URL manualmente para evitar fluxo PKCE no backend
    (que exigiria code_verifier no callback).
    """
    from apps.brands.models import Brand, BrandYouTubeCredential

    brand = Brand.objects.filter(id=int(brand_id)).first()
    youtube_credential = None
    if youtube_credential_id:
        youtube_credential = BrandYouTubeCredential.objects.filter(
            id=int(youtube_credential_id),
            brand_id=int(brand_id),
        ).first()
    config = get_client_config(brand=brand, youtube_credential=youtube_credential)
    if not config:
        raise ValueError("GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET devem estar configurados no .env")
    web = config["web"]
    params = {
        "client_id": web["client_id"],
        "redirect_uri": get_redirect_uri(brand=brand, youtube_credential=youtube_credential),
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": build_state_value(brand_id, youtube_credential_id),
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def fetch_tokens_and_channels(code: str, brand_id: int, youtube_credential_id: int | None = None) -> dict:
    """
    Troca code por tokens e lista canais do usuário.
    Retorna: {access_token, refresh_token, expires_at, channels: [{id, title}]}
    """
    from apps.brands.models import Brand, BrandYouTubeCredential

    brand = Brand.objects.filter(id=int(brand_id)).first()
    youtube_credential = None
    if youtube_credential_id:
        youtube_credential = BrandYouTubeCredential.objects.filter(
            id=int(youtube_credential_id),
            brand_id=int(brand_id),
        ).first()
    config = get_client_config(brand=brand, youtube_credential=youtube_credential)
    if not config:
        raise ValueError(
            "OAuth não configurado para a brand e sem fallback global "
            "(youtube_client_id/youtube_client_secret ou GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET)."
        )
    web = config["web"]
    token_resp = requests.post(
        web["token_uri"],
        data={
            "code": code,
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "redirect_uri": get_redirect_uri(brand=brand, youtube_credential=youtube_credential),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=web["token_uri"],
        client_id=web["client_id"],
        client_secret=web["client_secret"],
        scopes=YOUTUBE_SCOPES,
    )
    # Listar canais
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    channels = [
        {"id": item["id"], "title": item["snippet"]["title"]}
        for item in resp.get("items", [])
    ]
    expires_at = getattr(creds, "expiry", None)
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token or "",
        "expires_at": expires_at,
        "channels": channels,
    }


def get_check_client_config() -> dict | None:
    """Retorna config do YOUTUBE_CHECK_CLIENT_* para OAuth de busca de vídeos."""
    client_id = (os.getenv("YOUTUBE_CHECK_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("YOUTUBE_CHECK_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return None
    # Não usar YOUTUBE_REDIRECT_URI (callback de Contas); factory-check tem callback próprio
    redirect_uri = (
        (os.getenv("YOUTUBE_CHECK_REDIRECT_URI") or "").strip()
        or "http://127.0.0.1:8000/api/youtube/factory-check-callback/"
    )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        "redirect_uri": redirect_uri,
    }


def get_factory_check_authorization_url(factory_id: int) -> str:
    """URL de autorização OAuth para credencial de busca da factory."""
    config = get_check_client_config()
    if not config:
        raise ValueError("YOUTUBE_CHECK_CLIENT_ID e YOUTUBE_CHECK_CLIENT_SECRET devem estar no .env")
    params = {
        "client_id": config["web"]["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": f"{FACTORY_CHECK_STATE_PREFIX}{int(factory_id)}",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def fetch_tokens_for_factory_check(code: str, factory_id: int) -> dict:
    """Troca code por tokens para credencial de busca da factory."""
    config = get_check_client_config()
    if not config:
        raise ValueError("YOUTUBE_CHECK_CLIENT_ID e YOUTUBE_CHECK_CLIENT_SECRET devem estar no .env")
    web = config["web"]
    token_resp = requests.post(
        web["token_uri"],
        data={
            "code": code,
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "redirect_uri": config["redirect_uri"],
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=web["token_uri"],
        client_id=web["client_id"],
        client_secret=web["client_secret"],
        scopes=YOUTUBE_SCOPES,
    )
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    channels = [
        {"id": item["id"], "title": item["snippet"]["title"]}
        for item in resp.get("items", [])
    ]
    expires_at = getattr(creds, "expiry", None)
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token or "",
        "expires_at": expires_at,
        "channels": channels,
    }
