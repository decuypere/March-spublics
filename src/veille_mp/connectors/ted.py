"""Connecteur TED (Tenders Electronic Daily) - Office des publications de l'UE.

L'API de recherche est ouverte (pas de cle). Deux dialectes coexistent selon
les deploiements et les versions:

  * "v3"      POST https://api.ted.europa.eu/v3/notices/search
              corps {query, fields, page, limit, scope} -> {notices, totalNoticeCount}
  * "v3.0"    POST https://ted.europa.eu/api/v3.0/notices/search
              corps {q, fields, pageNum, pageSize, scope} -> {results, total}

Le connecteur essaie les endpoints configures dans l'ordre et retient le
premier qui repond. La normalisation est volontairement tolerante: depuis
2024 les avis sont publies au format eForms (noms de champs differents des
anciens XML), donc les champs sont retrouves par motif de cle plutot que par
nom exact, ce qui permet de couvrir les deux generations de format.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .base import Connector, ConnectorError
from ..models import Notice
from ..textutil import coerce_list, coerce_text, parse_amount, parse_date

# Codes ISO3 -> ISO2 (UE + EEE + quelques voisins). Sert a normaliser le
# champ pays, l'API TED utilisant l'ISO3 dans les requetes expertes.
ISO3_TO_ISO2 = {
    "AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ",
    "DNK": "DK", "EST": "EE", "FIN": "FI", "FRA": "FR", "DEU": "DE", "GRC": "GR",
    "HUN": "HU", "IRL": "IE", "ITA": "IT", "LVA": "LV", "LTU": "LT", "LUX": "LU",
    "MLT": "MT", "NLD": "NL", "POL": "PL", "PRT": "PT", "ROU": "RO", "SVK": "SK",
    "SVN": "SI", "ESP": "ES", "SWE": "SE", "ISL": "IS", "LIE": "LI", "NOR": "NO",
    "CHE": "CH", "GBR": "GB",
}

# Champs demandes a l'API. MINIMAL_FIELDS est le socle sur lequel on se replie
# si l'API rejette un nom de champ (HTTP 400): mieux vaut un avis sans budget
# qu'aucun avis. EXTRA_FIELDS apporte le budget estime et la description.
MINIMAL_FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "classification-cpv",
    "publication-date",
    "deadline-receipt-request",
    "notice-type",
    "links",
    "place-of-performance",
]

EXTRA_FIELDS = [
    "description-lot",
    "description-procedure",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "total-value",
    "notice-identifier",
]

DEFAULT_FIELDS = MINIMAL_FIELDS + EXTRA_FIELDS

LEGACY_FIELDS = ["ND", "TI", "PD", "CY", "AA", "CPV", "DT", "TD", "RC", "URI_DOC"]

# Motifs de recherche des champs, appliques aux cles de l'avis renvoye.
PATTERNS = {
    "publication_number": (r"^(publication-number|ND|nd|notice-identifier|noticeNumber)$",),
    "title": (r"title", r"^TI$"),
    "buyer": (r"buyer-?name", r"organisation-name-buyer", r"^AA$", r"contracting"),
    "cpv": (r"cpv",),
    "publication_date": (r"publication-?date", r"^PD$", r"dispatch-?date"),
    "deadline": (r"deadline", r"date-?receipt", r"^DT$", r"^DS$", r"time-?limit"),
    "country": (r"buyer-?country", r"^CY$", r"country"),
    "region": (r"nuts", r"place-of-performance", r"^RC$"),
    "value": (r"estimated-?value", r"^value-", r"total-?value"),
    "currency": (r"currency", r"-cur"),
    "notice_type": (r"notice-?type", r"^TD$", r"form-?type"),
    "description": (r"description",),
    "url": (r"^links$", r"^URI", r"^url$", r"uri-doc"),
}


def _find(payload: dict, kind: str) -> Any:
    """Retourne la premiere valeur non vide dont la cle correspond a un motif."""
    patterns = [re.compile(p, re.IGNORECASE) for p in PATTERNS[kind]]
    for pattern in patterns:
        for key, value in payload.items():
            if pattern.search(str(key)) and value not in (None, "", [], {}):
                return value
    return None


def _dedupe_parts(text: str) -> str:
    """TED repete les memes valeurs pour chaque lot: on ne garde qu'une
    occurrence de chaque, dans l'ordre d'apparition."""
    seen: list[str] = []
    for part in text.split(" | "):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return " | ".join(seen)


def _extract_url(payload: dict, publication_number: str) -> str:
    raw = _find(payload, "url")
    candidates: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("http"):
                candidates.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            # TED renvoie souvent {"html": {"FRA": "...", "ENG": "..."}, "pdf": {...}}
            for key in ("html", "HTML", "fra", "FRA", "fr", "eng", "ENG", "en"):
                if key in node:
                    walk(node[key])
                    if candidates:
                        return
            for value in node.values():
                walk(value)

    walk(raw)
    if candidates:
        return candidates[0]
    if publication_number:
        return f"https://ted.europa.eu/en/notice/-/detail/{publication_number}"
    return ""


class TedConnector(Connector):
    type_name = "ted"

    DIALECTS = {
        "v3": {
            "match": re.compile(r"/v3(?:/|$)"),
            "body": lambda q, fields, page, size: {
                "query": q,
                "fields": fields,
                "page": page,
                "limit": size,
                "scope": "ALL",
                "paginationMode": "PAGE_NUMBER",
                "checkQuerySyntax": False,
                "onlyLatestVersions": True,
            },
            "results_keys": ("notices", "results"),
            "total_keys": ("totalNoticeCount", "total", "totalResults"),
        },
        "v3.0": {
            "match": re.compile(r"/v3\.0(?:/|$)"),
            "body": lambda q, fields, page, size: {
                "q": q,
                "fields": fields,
                "pageNum": page,
                "pageSize": size,
                "scope": 3,
                "reverseOrder": True,
                "sortField": "PD",
            },
            "results_keys": ("results", "notices"),
            "total_keys": ("total", "totalNoticeCount"),
        },
    }

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.endpoints: list[str] = list(self.spec.get("endpoints") or [
            "https://api.ted.europa.eu/v3/notices/search",
            "https://ted.europa.eu/api/v3.0/notices/search",
        ])
        self.page_size = int(self.spec.get("page_size", 100))
        self.max_pages = int(self.spec.get("max_pages", 20))
        self.countries = [str(c).upper() for c in (self.spec.get("countries") or [])]

    # -- construction de la requete experte --------------------------------
    def _dialect_for(self, endpoint: str) -> tuple[str, dict]:
        for name, spec in self.DIALECTS.items():
            if spec["match"].search(endpoint):
                return name, spec
        return "v3", self.DIALECTS["v3"]

    def build_query(self, dialect: str, start: date, end: date) -> str:
        override = self.spec.get("expert_query")
        if override:
            return str(override)

        cpv = [re.sub(r"\D", "", str(c))[:8] for c in self.target_cpv]
        cpv = [c for c in cpv if c]

        if dialect == "v3.0":
            clauses = [f"PD=[{start:%Y%m%d} TO {end:%Y%m%d}]"]
            if cpv:
                clauses.append("PC=[" + " OR ".join(cpv) + "]")
            if self.countries:
                iso2 = [ISO3_TO_ISO2.get(c, c[:2]) for c in self.countries]
                clauses.append("CY=[" + " OR ".join(iso2) + "]")
            return " AND ".join(clauses)

        clauses = [f"publication-date>={start:%Y%m%d}", f"publication-date<={end:%Y%m%d}"]
        if cpv:
            clauses.append("classification-cpv IN (" + " ".join(cpv) + ")")
        if self.countries:
            iso3 = [c if len(c) == 3 else c for c in self.countries]
            clauses.append("buyer-country IN (" + " ".join(iso3) + ")")
        return " AND ".join(clauses)

    # -- appels ------------------------------------------------------------
    def _post(self, endpoint: str, body: dict) -> dict:
        response = self.http.post(endpoint, json=body,
                                  headers={"Content-Type": "application/json"})
        if response.status_code >= 400:
            error = ConnectorError(
                f"{endpoint} -> HTTP {response.status_code}: {response.text[:300]}"
            )
            error.status_code = response.status_code
            raise error
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(f"{endpoint}: reponse non JSON ({exc})") from exc

    @staticmethod
    def _first_key(payload: dict, keys: tuple[str, ...], default: Any) -> Any:
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        return default

    def fetch(self) -> list[Notice]:
        start, end = self.date_window()
        errors: list[str] = []

        for endpoint in self.endpoints:
            dialect_name, dialect = self._dialect_for(endpoint)
            fields = list(self.spec.get("fields") or
                          (LEGACY_FIELDS if dialect_name == "v3.0" else DEFAULT_FIELDS))
            query = self.build_query(dialect_name, start, end)
            self.log.info("TED [%s] %s | %s", dialect_name, endpoint, query)

            try:
                notices = self._fetch_with_field_fallback(
                    endpoint, dialect, dialect_name, query, fields
                )
            except Exception as exc:  # noqa: BLE001 - on essaie l'endpoint suivant
                errors.append(f"{endpoint}: {exc}")
                self.log.warning("Endpoint TED indisponible (%s), essai suivant", exc)
                continue
            return notices

        raise ConnectorError("Aucun endpoint TED disponible. " + " | ".join(errors))

    def _fetch_with_field_fallback(self, endpoint: str, dialect: dict, dialect_name: str,
                                   query: str, fields: list[str]) -> list[Notice]:
        """Un nom de champ inconnu fait echouer toute la requete. Dans ce cas
        on retente une fois avec le socle de champs, au lieu de perdre le run."""
        try:
            return self._fetch_all_pages(endpoint, dialect, dialect_name, query, fields)
        except ConnectorError as exc:
            status = getattr(exc, "status_code", None)
            fallback = LEGACY_FIELDS if dialect_name == "v3.0" else MINIMAL_FIELDS
            if status not in (400, 422) or fields == fallback:
                raise
            self.log.warning(
                "TED a rejete la liste de champs etendue (HTTP %s); repli sur le socle. "
                "Detail: %s", status, str(exc)[:200],
            )
            return self._fetch_all_pages(endpoint, dialect, dialect_name, query, fallback)

    def _fetch_all_pages(self, endpoint: str, dialect: dict, dialect_name: str,
                         query: str, fields: list[str]) -> list[Notice]:
        collected: list[Notice] = []
        seen_ids: set[str] = set()
        first_page = 1

        for page in range(first_page, first_page + self.max_pages):
            body = dialect["body"](query, fields, page, self.page_size)
            payload = self._post(endpoint, body)
            results = self._first_key(payload, dialect["results_keys"], [])
            if not isinstance(results, list) or not results:
                break

            for item in results:
                if not isinstance(item, dict):
                    continue
                notice = self.normalize(item)
                if notice.source_id in seen_ids:
                    continue
                seen_ids.add(notice.source_id)
                collected.append(notice)

            total = self._first_key(payload, dialect["total_keys"], None)
            if total is not None and len(seen_ids) >= int(total):
                break
            if len(results) < self.page_size:
                break

        self.log.info("TED: %d avis recuperes", len(collected))
        return collected

    # -- normalisation ------------------------------------------------------
    def normalize(self, payload: dict) -> Notice:
        publication_number = coerce_text(_find(payload, "publication_number")).split(" | ")[0]
        title = coerce_text(_find(payload, "title"))
        buyer = coerce_text(_find(payload, "buyer"))

        cpv_codes: list[str] = []
        for value in coerce_list(_find(payload, "cpv")):
            for match in re.findall(r"\d{8}", str(value)):
                if match not in cpv_codes:
                    cpv_codes.append(match)

        country_raw = coerce_text(_find(payload, "country")).split(" | ")[0].strip().upper()
        country = ISO3_TO_ISO2.get(country_raw, country_raw[:2] if country_raw else "")

        currency = coerce_text(_find(payload, "currency")).split(" | ")[0][:3].upper()

        return Notice(
            source=self.name,
            source_id=publication_number or coerce_text(payload.get("id")) or coerce_text(title)[:80],
            title=_dedupe_parts(title),
            url=_extract_url(payload, publication_number),
            buyer_name=_dedupe_parts(buyer),
            country=country,
            region=_dedupe_parts(coerce_text(_find(payload, "region")))[:200],
            cpv_codes=cpv_codes,
            publication_date=parse_date(_find(payload, "publication_date")),
            deadline=parse_date(_find(payload, "deadline")),
            value_amount=parse_amount(_find(payload, "value")),
            value_currency=currency,
            notice_type=coerce_text(_find(payload, "notice_type"))[:120],
            description=_dedupe_parts(coerce_text(_find(payload, "description")))[:5000],
            raw=payload,
        )

    # -- diagnostic ---------------------------------------------------------
    def check(self) -> tuple[bool, str]:
        start, end = self.date_window()
        for endpoint in self.endpoints:
            dialect_name, dialect = self._dialect_for(endpoint)
            fields = LEGACY_FIELDS if dialect_name == "v3.0" else MINIMAL_FIELDS
            body = dialect["body"](self.build_query(dialect_name, start, end), fields, 1, 1)
            try:
                payload = self._post(endpoint, body)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("check %s: %s", endpoint, exc)
                continue
            total = self._first_key(payload, dialect["total_keys"], "?")
            return True, f"OK via {endpoint} (dialecte {dialect_name}), {total} avis sur la fenetre"
        return False, "aucun endpoint TED n'a repondu (verifiez le reseau/proxy)"
