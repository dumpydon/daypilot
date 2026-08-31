from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import router
from backend.app.domain.models import (
    ConnectionCatalog,
    FileRoot,
    OAuthStartResponse,
    ProviderConnection,
    ProviderConnectionState,
)


class StubConnections:
    def catalog(self) -> ConnectionCatalog:
        return ConnectionCatalog(
            demo_mode=False,
            connections=[
                ProviderConnection(
                    service="mail",
                    provider="Gmail",
                    state=ProviderConnectionState.DISCONNECTED,
                    capabilities=["search_mail"],
                )
            ],
        )

    def start_google(self) -> OAuthStartResponse:
        return OAuthStartResponse(
            provider="google",
            authorization_url="https://accounts.google.com/auth",
            scopes=["openid"],
        )

    def start_x(self) -> OAuthStartResponse:
        return OAuthStartResponse(
            provider="x", authorization_url="https://x.com/auth", scopes=["users.read"]
        )

    async def disconnect_google(self) -> None:
        return None

    async def disconnect_x(self) -> None:
        return None

    async def list_file_roots(self) -> list[FileRoot]:
        return []

    async def add_file_root(self, path: str) -> FileRoot:
        return FileRoot(
            id="root-1", path=path, label="Notes", exists=True, added_at="2026-08-28T00:00:00Z"
        )

    async def remove_file_root(self, root_id: str) -> None:
        return None


def test_connection_management_endpoints_are_typed_and_separate() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.connections = StubConnections()
    app.state.settings = type("SettingsStub", (), {"site_url": "http://localhost:3000"})()
    client = TestClient(app)

    assert client.get("/api/connections").json()["demo_mode"] is False
    assert client.post("/api/connections/google/start").json()["provider"] == "google"
    assert client.post("/api/connections/x/start").json()["provider"] == "x"
    assert client.get("/api/connections/files/roots").json() == []
    assert (
        client.post("/api/connections/files/roots", json={"path": "/tmp/notes"}).status_code == 200
    )
    assert client.delete("/api/connections/files/roots/root-1").status_code == 204
