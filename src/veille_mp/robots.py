"""Verification robots.txt avant tout acces de type scraping."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)


class RobotsGate:
    """Cache de robots.txt. En cas d'echec de recuperation, on refuse par
    defaut (fail closed) pour ne pas risquer un acces non autorise."""

    def __init__(self, http_client, enabled: bool = True, fail_closed: bool = True):
        self.http = http_client
        self.enabled = enabled
        self.fail_closed = fail_closed
        self._cache: dict[str, RobotFileParser | None] = {}

    def _parser_for(self, url: str, robots_url: str | None = None) -> RobotFileParser | None:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._cache:
            return self._cache[origin]

        target = robots_url or urljoin(origin, "/robots.txt")
        parser: RobotFileParser | None = None
        try:
            response = self.http.get(target)
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
            elif response.status_code in (401, 403):
                parser = None  # acces interdit -> fail closed
            else:
                # 404 = pas de robots.txt = tout est autorise
                parser = RobotFileParser()
                parser.parse([])
        except Exception as exc:  # noqa: BLE001 - une source ne doit pas tout casser
            log.warning("robots.txt illisible pour %s: %s", origin, exc)
            parser = None

        self._cache[origin] = parser
        return parser

    def allowed(self, url: str, user_agent: str = "*", robots_url: str | None = None) -> bool:
        return self.describe(url, user_agent, robots_url)[0]

    def describe(self, url: str, user_agent: str = "*",
                 robots_url: str | None = None) -> tuple[bool, str]:
        """(autorise, motif). Distingue une interdiction explicite d'un
        robots.txt illisible (refus par defaut)."""
        if not self.enabled:
            return True, "verification robots.txt desactivee (http.respect_robots_txt)"
        parser = self._parser_for(url, robots_url)
        if parser is None:
            if self.fail_closed:
                return False, ("robots.txt illisible (reseau, proxy ou acces refuse): "
                               "acces refuse par defaut")
            return True, "robots.txt illisible, autorisation par defaut"
        if parser.can_fetch(user_agent, url):
            return True, "robots.txt autorise ce chemin"
        return False, "robots.txt interdit explicitement ce chemin pour ce User-Agent"

    def crawl_delay(self, url: str, user_agent: str = "*") -> float | None:
        parser = self._parser_for(url)
        if parser is None:
            return None
        try:
            delay = parser.crawl_delay(user_agent)
            return float(delay) if delay is not None else None
        except Exception:  # noqa: BLE001
            return None
