"""Doubles de test: client HTTP et garde robots.txt."""

from __future__ import annotations

import json
from typing import Any, Callable


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None,
                 text: str = "", headers: dict | None = None,
                 content: bytes | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload or {})
        self.headers = headers or {"Content-Type": "application/json"}
        self.content = content if content is not None else self.text.encode()

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("pas de JSON")
        return self._payload


class FakeHttpClient:
    """Renvoie des reponses scriptees et enregistre les appels."""

    def __init__(self, handler: Callable[[str, str, dict], FakeResponse]):
        self.handler = handler
        self.calls: list[tuple[str, str, dict]] = []
        self.session = _FakeSession()
        self.limiter = _FakeLimiter()

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.handler(method, url, kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        pass


class _FakeSession:
    headers = {"User-Agent": "veille-mp/test"}


class _FakeLimiter:
    min_interval = 0.0


class AllowAllRobots:
    def allowed(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def describe(self, *args: Any, **kwargs: Any) -> tuple[bool, str]:
        return True, "robots.txt autorise ce chemin"

    def crawl_delay(self, *args: Any, **kwargs: Any) -> float | None:
        return None


class DenyAllRobots:
    def allowed(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def describe(self, *args: Any, **kwargs: Any) -> tuple[bool, str]:
        return False, "robots.txt interdit explicitement ce chemin"

    def crawl_delay(self, *args: Any, **kwargs: Any) -> float | None:
        return None
