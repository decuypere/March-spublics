"""Client HTTP partage: throttling par domaine, retries, User-Agent explicite."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RateLimiter:
    """Un intervalle minimal entre deux requetes, par domaine."""

    def __init__(self, requests_per_second: float):
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = self.min_interval - (now - last)
            self._last[host] = now + max(0.0, delay)
        if delay > 0:
            time.sleep(delay)


class HttpClient:
    def __init__(self, cfg: dict):
        self.timeout = float(cfg.get("timeout_seconds", 45))
        self.max_retries = int(cfg.get("max_retries", 4))
        self.backoff_base = float(cfg.get("backoff_base_seconds", 2.0))
        self.limiter = RateLimiter(float(cfg.get("requests_per_second", 1.0)))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.get("user_agent", "veille-mp/0.1"),
            "Accept": "application/json, application/xml;q=0.9, */*;q=0.8",
        })

    def close(self) -> None:
        self.session.close()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        host = urlparse(url).netloc
        kwargs.setdefault("timeout", self.timeout)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self.limiter.wait(host)
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                self._sleep(attempt, None)
                log.warning("%s %s: %s (tentative %d)", method, url, exc, attempt + 1)
                continue

            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                log.warning("%s %s -> HTTP %s (tentative %d)", method, url,
                            response.status_code, attempt + 1)
                self._sleep(attempt, retry_after)
                continue
            return response

        if last_error:
            raise last_error
        raise RuntimeError(f"Echec de la requete {method} {url}")

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                time.sleep(min(60.0, float(retry_after)))
                return
            except ValueError:
                pass
        delay = self.backoff_base * (2 ** attempt) + random.uniform(0, 0.5)
        time.sleep(min(60.0, delay))

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)
