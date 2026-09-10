"""Connecteur de recherche HTTP pilote par la configuration.

Concu pour les plateformes sans API publique documentee (typiquement
e-Procurement / e-Notification, SPF BOSA). Rien n'est code en dur: endpoint,
methode, corps de requete, pagination et mapping des champs viennent du
fichier de configuration, ce qui permet d'adapter la source sans toucher au
code lorsque la plateforme evolue.

Garde-fous:
  * le robots.txt est verifie AVANT toute requete; un chemin interdit
    interrompt la source (et elle seule);
  * le throttling global du client HTTP s'applique;
  * la source est desactivee par defaut dans config.example.yaml: activez-la
    seulement apres avoir verifie les conditions d'utilisation de la
    plateforme.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

from .base import Connector, ConnectorError
from ..models import Notice
from ..textutil import (coerce_list, coerce_text, dig, parse_amount, parse_date,
                        sha1, strip_html)

DEFAULT_MAPPING = {
    "items": "items",
    "source_id": "id",
    "title": "title",
    "url": "url",
    "buyer_name": "buyer",
    "cpv_codes": "cpv[]",
    "publication_date": "publicationDate",
    "deadline": "deadline",
    "description": "description",
}


class HttpSearchConnector(Connector):
    type_name = "http_search"

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.base_url = str(self.spec.get("base_url", "")).rstrip("/")
        self.search_path = str(self.spec.get("search_path", ""))
        self.method = str(self.spec.get("method", "GET")).upper()
        self.mapping = {**DEFAULT_MAPPING, **(self.spec.get("mapping") or {})}
        pagination = self.spec.get("pagination") or {}
        self.page_param = pagination.get("page_param", "page")
        self.start_page = int(pagination.get("start_page", 0))
        self.max_pages = int(pagination.get("max_pages", 10))

    # -- url / garde robots -------------------------------------------------
    @property
    def search_url(self) -> str:
        if not self.base_url or not self.search_path:
            raise ConnectorError(
                f"Source '{self.name}': 'base_url' et 'search_path' sont requis. "
                "Verifiez d'abord l'endpoint reellement disponible (`veille doctor`)."
            )
        return f"{self.base_url}/{self.search_path.lstrip('/')}"

    def _robots_decision(self, url: str, user_agent: str,
                         robots_url: str | None) -> tuple[bool, str]:
        describe = getattr(self.robots, "describe", None)
        if describe is not None:
            return describe(url, user_agent, robots_url)
        allowed = self.robots.allowed(url, user_agent=user_agent, robots_url=robots_url)
        return allowed, "robots.txt autorise" if allowed else "robots.txt refuse"

    def _assert_allowed(self) -> None:
        url = self.search_url
        user_agent = self.http.session.headers.get("User-Agent", "*")
        robots_url = self.spec.get("robots_url")
        allowed, reason = self._robots_decision(url, user_agent, robots_url)
        if not allowed:
            raise ConnectorError(
                f"Source '{self.name}': acces a {url} non autorise -> {reason}. "
                "La source est ignoree (les autres continuent)."
            )
        delay = self.robots.crawl_delay(url, user_agent)
        if delay and delay > (self.http.limiter.min_interval or 0):
            self.http.limiter.min_interval = delay
            self.log.info("Crawl-delay robots.txt applique: %.1fs", delay)

    # -- requetes -----------------------------------------------------------
    def _render(self, template: Any, page: int, start: date, end: date) -> Any:
        """Remplace les placeholders {cpv_csv}, {date_from}, {date_to}, {page}."""
        values = {
            "cpv_csv": ",".join(re.sub(r"\D", "", c)[:8] for c in self.target_cpv),
            "cpv_space": " ".join(re.sub(r"\D", "", c)[:8] for c in self.target_cpv),
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "date_from_compact": f"{start:%Y%m%d}",
            "date_to_compact": f"{end:%Y%m%d}",
            "page": page,
        }
        if isinstance(template, str):
            try:
                return template.format(**values)
            except (KeyError, IndexError, ValueError):
                return template
        if isinstance(template, list):
            return [self._render(item, page, start, end) for item in template]
        if isinstance(template, dict):
            return {k: self._render(v, page, start, end) for k, v in template.items()}
        return template

    @staticmethod
    def _declared_page_size(request_spec: dict) -> int | None:
        """Taille de page annoncee dans la config, pour savoir quand s'arreter."""
        for container in (request_spec.get("json"), request_spec.get("params")):
            if not isinstance(container, dict):
                continue
            for key in ("size", "pageSize", "limit", "per_page"):
                if isinstance(container.get(key), int):
                    return container[key]
        return None

    def fetch(self) -> list[Notice]:
        self._assert_allowed()
        start, end = self.date_window()
        request_spec = self.spec.get("request") or {}
        collected: list[Notice] = []
        seen: set[str] = set()
        page_size = self._declared_page_size(request_spec)

        for offset in range(self.max_pages):
            page = self.start_page + offset
            kwargs: dict[str, Any] = {}
            for key in ("json", "params", "data", "headers"):
                if key in request_spec:
                    kwargs[key] = self._render(copy.deepcopy(request_spec[key]), page, start, end)
            container = kwargs.get("json") if self.method == "POST" else kwargs.setdefault("params", {})
            if isinstance(container, dict) and self.page_param:
                container[self.page_param] = page

            response = self.http.request(self.method, self.search_url, **kwargs)
            if response.status_code >= 400:
                raise ConnectorError(
                    f"{self.search_url} -> HTTP {response.status_code}: {response.text[:300]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ConnectorError(
                    f"{self.search_url}: reponse non JSON. Cette plateforme renvoie "
                    f"probablement du HTML; adaptez la configuration ou ecrivez un "
                    f"connecteur dedie. ({exc})"
                ) from exc

            items = dig(payload, self.mapping["items"])
            if not isinstance(items, list) or not items:
                break

            for item in items:
                notice = self.normalize(item)
                if notice.source_id in seen:
                    continue
                seen.add(notice.source_id)
                collected.append(notice)

            if page_size and len(items) < page_size:
                break

        self.log.info("%s: %d avis recuperes", self.name, len(collected))
        return collected

    # -- normalisation ------------------------------------------------------
    def _map(self, item: dict, field: str) -> Any:
        path = self.mapping.get(field)
        return dig(item, path) if path else None

    def normalize(self, item: dict) -> Notice:
        title = strip_html(coerce_text(self._map(item, "title")))
        url = coerce_text(self._map(item, "url"))
        if url and url.startswith("/"):
            url = f"{self.base_url}{url}"

        source_id = coerce_text(self._map(item, "source_id")) or sha1(self.name, title, url)

        cpv_codes: list[str] = []
        for value in coerce_list(self._map(item, "cpv_codes")):
            for match in re.findall(r"\d{8}", str(value)):
                if match not in cpv_codes:
                    cpv_codes.append(match)

        return Notice(
            source=self.name,
            source_id=source_id[:300],
            title=title,
            url=url,
            buyer_name=strip_html(coerce_text(self._map(item, "buyer_name"))),
            country=str(self.spec.get("country", "")).upper(),
            region=coerce_text(self._map(item, "region"))[:200],
            cpv_codes=cpv_codes,
            publication_date=parse_date(self._map(item, "publication_date")),
            deadline=parse_date(self._map(item, "deadline")),
            value_amount=parse_amount(self._map(item, "value_amount")),
            value_currency=coerce_text(self._map(item, "value_currency"))[:3].upper(),
            notice_type=coerce_text(self._map(item, "notice_type"))[:120],
            description=strip_html(coerce_text(self._map(item, "description")))[:5000],
            raw=item,
        )

    # -- diagnostic ---------------------------------------------------------
    def check(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "base_url non configuree"
        user_agent = self.http.session.headers.get("User-Agent", "*")
        robots_url = self.spec.get("robots_url")

        target = self.search_url if self.search_path else self.base_url
        try:
            allowed, reason = self._robots_decision(target, user_agent, robots_url)
        except ConnectorError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, f"robots.txt inaccessible: {exc}"

        if not allowed:
            return False, (f"{reason} -> ne pas activer cette source sans avoir "
                           "verifie les CGU et l'accord de la plateforme")
        if not self.search_path:
            return False, ("robots.txt permissif, mais 'search_path' n'est pas renseigne: "
                           "aucune API publique documentee n'a ete confirmee")
        try:
            response = self.http.request(self.method, self.search_url,
                                         **({"json": (self.spec.get("request") or {}).get("json", {})}
                                            if self.method == "POST" else {}))
        except Exception as exc:  # noqa: BLE001
            return False, f"endpoint injoignable: {exc}"
        content_type = response.headers.get("Content-Type", "")
        ok = response.status_code < 400 and "json" in content_type.lower()
        return ok, f"HTTP {response.status_code}, Content-Type: {content_type or 'inconnu'}"
