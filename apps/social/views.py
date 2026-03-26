"""Views para OAuth e contas sociais."""
import uuid

from django.core.cache import cache
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.brands.models import (
    Brand,
    BrandSocialAccount,
    BrandYouTubeCredential,
    Factory,
    FactoryYouTubeCheckCredential,
)
from apps.social.services.youtube_oauth import (
    FACTORY_CHECK_STATE_PREFIX,
    fetch_tokens_and_channels,
    fetch_tokens_for_factory_check,
    get_authorization_url,
    get_client_config,
    get_factory_check_authorization_url,
    parse_state_value,
)

CACHE_PREFIX = "youtube_oauth:"
CACHE_TIMEOUT = 900  # 15 min


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def youtube_connect(request):
    """Inicia fluxo OAuth. Query: ?brand_id=X"""
    brand_id = request.query_params.get("brand_id")
    youtube_credential_id = request.query_params.get("youtube_credential_id")
    if not brand_id:
        return Response(
            {"error": "brand_id é obrigatório"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    cred = None
    try:
        brand = Brand.objects.get(id=int(brand_id))
        if youtube_credential_id:
            cred = BrandYouTubeCredential.objects.get(
                id=int(youtube_credential_id),
                brand=brand,
            )
    except (ValueError, Brand.DoesNotExist):
        return Response(
            {"error": "Marca não encontrada"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except BrandYouTubeCredential.DoesNotExist:
        return Response(
            {"error": "Credencial YouTube não encontrada para a marca"},
            status=status.HTTP_404_NOT_FOUND,
        )
    try:
        config = get_client_config(brand=brand, youtube_credential=cred)
    except ValueError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not config:
        return Response(
            {"error": "OAuth não configurado (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    url = get_authorization_url(brand.id, cred.id if cred else None)
    return redirect(url)


@api_view(["GET"])
@permission_classes([AllowAny])  # Callback vem do redirect do Google; sem cookie JWT
def youtube_callback(request):
    """Callback OAuth. Recebe code e state (brand_id). Cria/atualiza BrandSocialAccount."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error_param = request.query_params.get("error")
    if error_param:
        return redirect(_frontend_url(f"/contas?error={error_param}"))
    if not code or not state:
        return redirect(_frontend_url("/contas?error=missing_code_or_state"))
    youtube_credential = None
    try:
        brand_id, youtube_credential_id = parse_state_value(state)
        brand = Brand.objects.get(id=brand_id)
        if youtube_credential_id:
            youtube_credential = BrandYouTubeCredential.objects.get(
                id=youtube_credential_id,
                brand=brand,
            )
    except (ValueError, Brand.DoesNotExist):
        return redirect(_frontend_url("/contas?error=invalid_brand"))
    except BrandYouTubeCredential.DoesNotExist:
        return redirect(_frontend_url("/contas?error=invalid_youtube_credential"))
    try:
        data = fetch_tokens_and_channels(
            code,
            brand_id,
            youtube_credential.id if youtube_credential else None,
        )
    except Exception as e:
        return redirect(_frontend_url(f"/contas?error=oauth_failed&detail={str(e)[:50]}"))
    channels = data["channels"]
    if not channels:
        return redirect(_frontend_url("/contas?error=no_channels"))
    # Se só um canal, salva direto. Se vários, redireciona para frontend escolher
    if len(channels) == 1:
        _save_youtube_account(brand, channels[0], data, youtube_credential=youtube_credential)
        return redirect(_frontend_url("/contas?youtube_connected=1"))
    # Múltiplos canais: salva em cache e redireciona para escolher
    key = str(uuid.uuid4())
    cache.set(
        CACHE_PREFIX + key,
        {
            "brand_id": brand_id,
            "youtube_credential_id": youtube_credential.id if youtube_credential else None,
            "channels": channels,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": data["expires_at"].isoformat() if data["expires_at"] else None,
        },
        CACHE_TIMEOUT,
    )
    return redirect(_frontend_url(f"/contas?youtube_choose_channel=1&key={key}"))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def youtube_pending_channels(request):
    """Retorna canais pendentes de seleção (quando há múltiplos). Query: ?key=UUID"""
    key = request.query_params.get("key")
    if not key:
        return Response({"channels": []})
    data = cache.get(CACHE_PREFIX + key)
    if not data:
        return Response({"channels": [], "expired": True})
    return Response({"channels": data["channels"], "brand_id": data["brand_id"], "key": key})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def youtube_select_channel(request):
    """Seleciona canal quando há múltiplos. Body: {channel_id, channel_title, key}"""
    key = request.data.get("key")
    if not key:
        return Response(
            {"error": "key é obrigatório (da URL ?key=...)"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    data = cache.get(CACHE_PREFIX + key)
    if not data:
        return Response(
            {"error": "Seleção expirada. Conecte novamente."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    channel_id = request.data.get("channel_id")
    channel_title = request.data.get("channel_title", "")
    if not channel_id:
        return Response(
            {"error": "channel_id é obrigatório"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    valid = next((c for c in data["channels"] if c["id"] == channel_id), None)
    if not valid:
        return Response(
            {"error": "Canal inválido"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    brand = Brand.objects.get(id=data["brand_id"])
    from datetime import datetime

    expires_at = None
    if data.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (ValueError, TypeError):
            pass
    _save_youtube_account(
        brand,
        {"id": channel_id, "title": channel_title or valid["title"]},
        {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": expires_at,
        },
        youtube_credential=(
            BrandYouTubeCredential.objects.filter(
                id=data.get("youtube_credential_id"),
                brand=brand,
            ).first()
            if data.get("youtube_credential_id")
            else None
        ),
    )
    cache.delete(CACHE_PREFIX + key)
    return Response({"status": "ok", "channel_id": channel_id})


def _save_youtube_account(brand, channel, token_data, youtube_credential=None):
    """Cria ou atualiza BrandSocialAccount para YouTube."""
    from datetime import datetime

    expires_at = token_data.get("expires_at")
    if expires_at and not isinstance(expires_at, datetime):
        try:
            expires_at = datetime.fromisoformat(str(expires_at))
        except (ValueError, TypeError):
            expires_at = None
    platform = "YTB"  # YouTube longos; YT (Shorts) usa mesma conta
    obj, _ = BrandSocialAccount.objects.update_or_create(
        brand=brand,
        platform=platform,
        channel_id=channel["id"],
        defaults={
            "account_name": channel.get("title", ""),
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_at": expires_at,
        },
    )
    if youtube_credential is not None:
        youtube_credential.channel_id = channel.get("id", "")
        youtube_credential.account_name = channel.get("title", "")
        youtube_credential.access_token = token_data.get("access_token", "")
        youtube_credential.refresh_token = token_data.get("refresh_token", "")
        youtube_credential.expires_at = expires_at
        youtube_credential.last_error = ""
        youtube_credential.save(
            update_fields=[
                "channel_id",
                "account_name",
                "access_token",
                "refresh_token",
                "expires_at",
                "last_error",
                "updated_at",
            ]
        )
    return obj


def _frontend_url(path: str) -> str:
    """URL do frontend para redirect após OAuth."""
    import os

    base = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return f"{base.rstrip('/')}{path}"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def factory_check_connect(request):
    """Inicia OAuth para credencial de busca da factory. Query: ?factory_id=X"""
    factory_id = request.query_params.get("factory_id")
    if not factory_id:
        return Response(
            {"error": "factory_id é obrigatório"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        factory = Factory.objects.get(id=int(factory_id))
    except (ValueError, Factory.DoesNotExist):
        return Response(
            {"error": "Factory não encontrada"},
            status=status.HTTP_404_NOT_FOUND,
        )
    try:
        url = get_factory_check_authorization_url(factory.id)
        return redirect(url)
    except ValueError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def factory_check_callback(request):
    """Callback OAuth para credencial de busca da factory."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error_param = request.query_params.get("error")
    base_path = "/canais-busca"
    if error_param:
        return redirect(_frontend_url(f"{base_path}?error={error_param}"))
    if not code or not state or not state.startswith(FACTORY_CHECK_STATE_PREFIX):
        return redirect(_frontend_url(f"{base_path}?error=missing_code_or_state"))
    try:
        factory_id = int(state[len(FACTORY_CHECK_STATE_PREFIX) :])
        factory = Factory.objects.get(id=factory_id)
    except (ValueError, Factory.DoesNotExist):
        return redirect(_frontend_url(f"{base_path}?error=invalid_factory"))
    try:
        data = fetch_tokens_for_factory_check(code, factory_id)
    except Exception as e:
        return redirect(_frontend_url(f"{base_path}?error=oauth_failed&detail={str(e)[:50]}"))
    channels = data.get("channels") or []
    if not channels:
        return redirect(_frontend_url(f"{base_path}?error=no_channels"))
    channel = channels[0]
    if len(channels) > 1:
        channel = next((c for c in channels if "YouTube" in c.get("title", "")), channels[0])
    cred, _ = FactoryYouTubeCheckCredential.objects.update_or_create(
        factory=factory,
        defaults={
            "channel_id": channel.get("id", ""),
            "account_name": channel.get("title", ""),
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": data.get("expires_at"),
        },
    )
    return redirect(_frontend_url(f"{base_path}?youtube_check_connected=1&factory_id={factory_id}"))
