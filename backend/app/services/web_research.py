from __future__ import annotations

from typing import Any

import httpx

from backend.app.config import Settings
from backend.app.domain.errors import ProviderUnavailableError


class WebResearchService:
    """One bounded Tavily search behind DayPilot's semantic web capability."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    def search_web(self, query: str, limit: int = 5) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("Web search requires a non-empty query")
        if not self.settings.tavily_api_key:
            raise ProviderUnavailableError(
                "Fresh web research is unavailable because TAVILY_API_KEY is not configured."
            )
        limit = max(1, min(limit, 8))
        try:
            with httpx.Client(
                base_url=self.settings.tavily_base_url.rstrip("/"),
                headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
                timeout=self.settings.provider_http_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    "/search",
                    json={
                        "query": query,
                        "search_depth": "basic",
                        "max_results": limit,
                        "include_answer": True,
                        "include_raw_content": False,
                    },
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Fresh web research could not be reached. Try again shortly."
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderUnavailableError(
                "Fresh web research authorization failed. Check TAVILY_API_KEY."
            )
        if response.status_code == 429:
            raise ProviderUnavailableError("Fresh web research is rate limited. Try again shortly.")
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                f"Fresh web research failed with provider status {response.status_code}."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(
                "Fresh web research returned an unreadable response."
            ) from exc
        raw_results = payload.get("results") if isinstance(payload, dict) else []
        sources: list[dict[str, Any]] = []
        for item in raw_results if isinstance(raw_results, list) else []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url.startswith(("https://", "http://")) or not title:
                continue
            sources.append(
                {
                    "title": title[:300],
                    "url": url,
                    "snippet": str(item.get("content") or "").strip()[:2_000],
                    "score": item.get("score"),
                }
            )
        answer = payload.get("answer") if isinstance(payload, dict) else None
        return {
            "query": query,
            "answer": str(answer).strip() if answer else None,
            "sources": sources,
            "count": len(sources),
            "provider": "Tavily",
            "source": "web",
            "connection_mode": "direct",
            "real": True,
        }
