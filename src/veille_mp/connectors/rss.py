"""Connecteur RSS/Atom generique.

Utile pour les flux d'e-Notification / Bulletin des Adjudications ou pour
toute plateforme exposant une recherche sauvegardee sous forme de flux.
Configurer simplement une liste d'URLs dans `feeds`.
"""

from __future__ import annotations

import re
from typing import Any

import feedparser

from .base import Connector, ConnectorError
from ..models import Notice
from ..textutil import parse_amount, parse_date, sha1, strip_html

CPV_RE = re.compile(r"\b\d{8}\b")
DEADLINE_RE = re.compile(
    r"(?:date limite|limite de r[eé]ception|uiterste datum|deadline|indienings?datum)"
    r"[^0-9]{0,40}(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


class RssConnector(Connector):
    type_name = "rss"

    def fetch(self) -> list[Notice]:
        feeds = list(self.spec.get("feeds") or [])
        if not feeds:
            raise ConnectorError(
                f"Source '{self.name}': aucun flux configure (champ 'feeds'). "
                "Lancez `veille doctor` pour verifier la disponibilite des flux."
            )

        notices: list[Notice] = []
        errors: list[str] = []
        for url in feeds:
            try:
                notices.extend(self._fetch_feed(url))
            except Exception as exc:  # noqa: BLE001 - un flux ne bloque pas les autres
                errors.append(f"{url}: {exc}")
                self.log.warning("Flux indisponible %s: %s", url, exc)

        if not notices and errors:
            raise ConnectorError("; ".join(errors))
        return notices

    def _fetch_feed(self, url: str) -> list[Notice]:
        response = self.http.get(url)
        if response.status_code >= 400:
            raise ConnectorError(f"HTTP {response.status_code}")
        parsed = feedparser.parse(response.content)
        if parsed.bozo and not parsed.entries:
            raise ConnectorError(f"flux illisible ({parsed.get('bozo_exception')})")
        return [self.normalize(entry, url) for entry in parsed.entries]

    def normalize(self, entry: Any, feed_url: str) -> Notice:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "") or ""
        summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
        content = ""
        for block in entry.get("content", []) or []:
            content += " " + strip_html(block.get("value", ""))
        body = f"{summary} {content}".strip()

        source_id = entry.get("id") or entry.get("guid") or link or sha1(feed_url, title)

        cpv_codes: list[str] = []
        for match in CPV_RE.findall(f"{title} {body}"):
            if match not in cpv_codes:
                cpv_codes.append(match)

        deadline = None
        found = DEADLINE_RE.search(body)
        if found:
            deadline = parse_date(found.group(1))

        published = entry.get("published") or entry.get("updated") or entry.get("created")

        return Notice(
            source=self.name,
            source_id=str(source_id)[:300],
            title=title,
            url=link,
            buyer_name=strip_html(entry.get("author", "") or ""),
            country=str(self.spec.get("country", "")).upper(),
            cpv_codes=cpv_codes,
            publication_date=parse_date(published),
            deadline=deadline,
            value_amount=parse_amount(self._value_hint(body)),
            value_currency="EUR" if "eur" in body.lower() or "€" in body else "",
            description=body[:5000],
            raw={"feed": feed_url, "entry": {k: str(v)[:2000] for k, v in entry.items()}},
        )

    @staticmethod
    def _value_hint(body: str) -> str | None:
        match = re.search(r"(?:montant|valeur|waarde|value)[^0-9]{0,30}([\d .,]{4,20})",
                          body, re.IGNORECASE)
        return match.group(1) if match else None

    def check(self) -> tuple[bool, str]:
        feeds = list(self.spec.get("feeds") or [])
        if not feeds:
            return False, "aucun flux configure (champ 'feeds' vide)"
        oks, kos = [], []
        for url in feeds:
            try:
                response = self.http.get(url)
                parsed = feedparser.parse(response.content)
                if response.status_code < 400 and parsed.entries:
                    oks.append(f"{url} ({len(parsed.entries)} entrees)")
                else:
                    kos.append(f"{url} (HTTP {response.status_code}, {len(parsed.entries)} entrees)")
            except Exception as exc:  # noqa: BLE001
                kos.append(f"{url}: {exc}")
        message = "; ".join(["OK: " + ", ".join(oks)] if oks else []) + \
                  ("; KO: " + ", ".join(kos) if kos else "")
        return bool(oks), message or "aucun flux joignable"
