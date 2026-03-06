"""Serviço OAuth para YouTube."""
import os
from urllib.parse import urlencode

import requests
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def get_client_config():
    """Retorna client_config a partir de variáveis de ambiente."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    redirect_uri = os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/callback/")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def get_redirect_uri():
    return os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:8000/api/youtube/callback/")


def get_authorization_url(brand_id: int) -> str:
    """Retorna URL para o usuário autorizar no Google. state=brand_id.

    Montamos a URL manualmente para evitar fluxo PKCE no backend
    (que exigiria code_verifier no callback).
    """
    config = get_client_config()
    if not config:
        raise ValueError("GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET devem estar configurados no .env")
    web = config["web"]
    params = {
        "client_id": web["client_id"],
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": str(brand_id),
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def fetch_tokens_and_channels(code: str, brand_id: int) -> dict:
    """
    Troca code por tokens e lista canais do usuário.
    Retorna: {access_token, refresh_token, expires_at, channels: [{id, title}]}
    """
    del brand_id  # state já foi validado na view de callback
    config = get_client_config()
    if not config:
        raise ValueError("OAuth não configurado (GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET)")
    web = config["web"]
    token_resp = requests.post(
        web["token_uri"],
        data={
            "code": code,
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "redirect_uri": get_redirect_uri(),
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
