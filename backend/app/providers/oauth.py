from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.app.domain.errors import OAuthError, ProviderUnavailableError
from backend.app.providers.models import CredentialRecord

GOOGLE_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
)
X_SCOPES = (
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",
)


@dataclass(frozen=True)
class OAuthFlow:
    provider: str
    state: str
    verifier: str
    created_at: float


class OAuthFlowStore:
    """Short-lived server-side OAuth state and PKCE verifier storage."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = ttl_seconds
        self._flows: dict[str, OAuthFlow] = {}

    def create(self, provider: str) -> OAuthFlow:
        self._prune()
        flow = OAuthFlow(
            provider=provider,
            state=secrets.token_urlsafe(32),
            verifier=secrets.token_urlsafe(64),
            created_at=time.time(),
        )
        self._flows[flow.state] = flow
        return flow

    def consume(self, provider: str, state: str) -> OAuthFlow:
        self._prune()
        flow = self._flows.pop(state, None)
        if flow is None or flow.provider != provider:
            raise OAuthError(
                "The OAuth state is invalid or has expired. Start the connection again."
            )
        return flow

    def _prune(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for state, flow in list(self._flows.items()):
            if flow.created_at < cutoff:
                self._flows.pop(state, None)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def google_authorization_url(client_id: str, redirect_uri: str, flow: OAuthFlow) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": flow.state,
        "code_challenge": pkce_challenge(flow.verifier),
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query)


def x_authorization_url(client_id: str, redirect_uri: str, flow: OAuthFlow) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(X_SCOPES),
        "state": flow.state,
        "code_challenge": pkce_challenge(flow.verifier),
        "code_challenge_method": "S256",
    }
    return "https://x.com/i/oauth2/authorize?" + urlencode(query)


async def exchange_code(
    *,
    provider: str,
    code: str,
    verifier: str,
    client_id: str,
    client_secret: str | None,
    redirect_uri: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if provider == "google":
        url = "https://oauth2.googleapis.com/token"
    elif provider == "x":
        url = "https://api.x.com/2/oauth2/token"
    else:
        raise OAuthError("Unsupported OAuth provider")
    data = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            response = await client.post(url, data=data)
    except httpx.RequestError as exc:
        raise OAuthError("The provider authorization server could not be reached.") from exc
    if response.status_code >= 400:
        raise OAuthError(_oauth_error_message(response))
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthError("The provider returned an invalid OAuth response.") from exc
    if not payload.get("access_token"):
        raise OAuthError("The provider did not return an access token.")
    return payload


async def refresh_access_token(
    *,
    provider: str,
    credential: CredentialRecord,
    client_id: str,
    client_secret: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CredentialRecord:
    if not credential.refresh_token:
        raise ProviderUnavailableError(
            f"{provider.title()} authorization expired. Reconnect required.",
            requires_reauth=True,
        )
    url = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else "https://api.x.com/2/oauth2/token"
    )
    data = {
        "grant_type": "refresh_token",
        "refresh_token": credential.refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            response = await client.post(url, data=data)
    except httpx.RequestError as exc:
        raise ProviderUnavailableError(
            f"{provider.title()} could not refresh its authorization. Try reconnecting.",
            requires_reauth=True,
        ) from exc
    if response.status_code >= 400:
        raise ProviderUnavailableError(
            f"{provider.title()} authorization expired or was revoked. Reconnect required.",
            requires_reauth=True,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderUnavailableError(
            f"{provider.title()} returned an invalid refresh response.", requires_reauth=True
        ) from exc
    access_token = payload.get("access_token")
    if not access_token:
        raise ProviderUnavailableError(
            f"{provider.title()} did not return a refreshed access token.", requires_reauth=True
        )
    expires_in = payload.get("expires_in")
    return CredentialRecord(
        provider=credential.provider,
        access_token=access_token,
        refresh_token=payload.get("refresh_token") or credential.refresh_token,
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=tuple(payload.get("scope", "").split()) or credential.scopes,
        account_label=credential.account_label,
        metadata=credential.metadata,
        last_error=None,
    )


async def profile_for(
    provider: str,
    access_token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    url = (
        "https://openidconnect.googleapis.com/v1/userinfo"
        if provider == "google"
        else "https://api.x.com/2/users/me"
    )
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.RequestError as exc:
        raise OAuthError("The provider account profile could not be loaded.") from exc
    if response.status_code >= 400:
        raise OAuthError("Authorization succeeded, but the provider account could not be verified.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthError("The provider returned an invalid account profile.") from exc
    return payload.get("data", payload)


def _oauth_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "Provider authorization was rejected."
    error = payload.get("error_description") or payload.get("error")
    return (
        f"Provider authorization failed: {error}."
        if error
        else "Provider authorization was rejected."
    )
