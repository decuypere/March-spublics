"""Le reseau n'est pas sollicite: les deux dialectes de l'API TED sont
simules, y compris la forme eForms (valeurs multilingues) apparue en 2024."""

from datetime import date

import pytest

from veille_mp.connectors.ted import TedConnector
from veille_mp.connectors.base import ConnectorError
from tests.fakes import AllowAllRobots, FakeHttpClient, FakeResponse

GLOBAL_CFG = {"filtering": {"cpv_codes": ["71200000", "71220000"]}}

SPEC = {
    "name": "ted",
    "type": "ted",
    "lookback_days": 7,
    "page_size": 2,
    "max_pages": 5,
    "countries": ["BEL"],
    "endpoints": [
        "https://api.ted.europa.eu/v3/notices/search",
        "https://ted.europa.eu/api/v3.0/notices/search",
    ],
}

# Forme eForms: chaque champ est un dict langue -> liste de valeurs.
EFORMS_NOTICE = {
    "publication-number": "00512345-2026",
    "notice-title": {"fra": ["Mission complete d'architecture - ecole"],
                     "eng": ["Full architectural services - school"]},
    "buyer-name": {"fra": ["Commune d'Ixelles"]},
    "buyer-country": ["BEL"],
    "classification-cpv": ["71200000", "71221000-3"],
    "publication-date": "2026-09-08+02:00",
    "deadline-receipt-request": "2026-10-20Z",
    "notice-type": {"eng": ["cn-standard"]},
    "links": {"html": {"FRA": "https://ted.europa.eu/fr/notice/-/detail/00512345-2026"}},
    "description-lot": {"fra": ["Esquisse, avant-projet et suivi de chantier."]},
    "estimated-value-lot": "850000",
    "estimated-value-cur-lot": "EUR",
}

# Forme heritee (avant eForms): champs plats a codes courts.
LEGACY_NOTICE = {
    "ND": "412345-2023",
    "TI": "BE-Namur: Services d'architecture paysagere",
    "AA": "Ville de Namur",
    "CY": "BE",
    "CPV": "71420000",
    "PD": "20230915",
    "DT": "20231020",
    "TD": "3",
    "URI_DOC": "https://ted.europa.eu/udl?uri=TED:NOTICE:412345-2023",
}


def build(handler, spec=None):
    http = FakeHttpClient(handler)
    connector = TedConnector(spec or SPEC, http, AllowAllRobots(), GLOBAL_CFG)
    return connector, http


def test_expert_query_v3_uses_cpv_country_and_date_window():
    connector, _ = build(lambda *a: FakeResponse(payload={}))
    query = connector.build_query("v3", date(2026, 9, 1), date(2026, 9, 8))
    assert "classification-cpv IN (71200000 71220000)" in query
    assert "buyer-country IN (BEL)" in query
    assert "publication-date>=20260901" in query
    assert "publication-date<=20260908" in query


def test_expert_query_legacy_dialect_uses_short_codes_and_iso2():
    connector, _ = build(lambda *a: FakeResponse(payload={}))
    query = connector.build_query("v3.0", date(2026, 9, 1), date(2026, 9, 8))
    assert "PD=[20260901 TO 20260908]" in query
    assert "PC=[71200000 OR 71220000]" in query
    assert "CY=[BE]" in query  # ISO3 -> ISO2


def test_expert_query_override_is_respected():
    spec = {**SPEC, "expert_query": "classification-cpv IN (71200000)"}
    connector, _ = build(lambda *a: FakeResponse(payload={}), spec)
    assert connector.build_query("v3", date(2026, 9, 1), date(2026, 9, 8)) == \
        "classification-cpv IN (71200000)"


def test_eforms_notice_is_normalized():
    def handler(method, url, kwargs):
        page = kwargs["json"].get("page", 1)
        notices = [EFORMS_NOTICE] if page == 1 else []
        return FakeResponse(payload={"notices": notices, "totalNoticeCount": 1})

    connector, _ = build(handler)
    notices = connector.fetch()

    assert len(notices) == 1
    notice = notices[0]
    assert notice.source_id == "00512345-2026"
    assert notice.title == "Mission complete d'architecture - ecole"
    assert notice.buyer_name == "Commune d'Ixelles"
    assert notice.country == "BE"                       # BEL -> BE
    assert notice.cpv_codes == ["71200000", "71221000"]  # tiret de controle retire
    assert notice.publication_date == date(2026, 9, 8)
    assert notice.deadline == date(2026, 10, 20)
    assert notice.value_amount == 850000.0
    assert notice.value_currency == "EUR"
    assert notice.url.startswith("https://ted.europa.eu/fr/notice")
    assert "Esquisse" in notice.description


def test_falls_back_to_legacy_endpoint_when_first_one_fails():
    def handler(method, url, kwargs):
        if "api.ted.europa.eu" in url:
            return FakeResponse(status_code=503, payload=None, text="Service Unavailable")
        page = kwargs["json"].get("pageNum", 1)
        results = [LEGACY_NOTICE] if page == 1 else []
        return FakeResponse(payload={"results": results, "total": 1})

    connector, http = build(handler)
    notices = connector.fetch()

    assert [c[1] for c in http.calls][0].startswith("https://api.ted.europa.eu")
    assert len(notices) == 1
    notice = notices[0]
    assert notice.source_id == "412345-2023"
    assert notice.country == "BE"
    assert notice.cpv_codes == ["71420000"]
    assert notice.publication_date == date(2023, 9, 15)
    assert notice.deadline == date(2023, 10, 20)
    assert notice.url.startswith("https://ted.europa.eu/udl")


def test_url_is_rebuilt_from_publication_number_when_links_are_missing():
    payload = {k: v for k, v in EFORMS_NOTICE.items() if k != "links"}

    def handler(method, url, kwargs):
        page = kwargs["json"].get("page", 1)
        return FakeResponse(payload={"notices": [payload] if page == 1 else []})

    connector, _ = build(handler)
    notice = connector.fetch()[0]
    assert notice.url == "https://ted.europa.eu/en/notice/-/detail/00512345-2026"


def test_pagination_stops_on_total_and_deduplicates_ids():
    pages = {
        1: [EFORMS_NOTICE, {**EFORMS_NOTICE, "publication-number": "00512346-2026"}],
        2: [{**EFORMS_NOTICE, "publication-number": "00512347-2026"}],
    }

    def handler(method, url, kwargs):
        page = kwargs["json"].get("page", 1)
        return FakeResponse(payload={"notices": pages.get(page, []), "totalNoticeCount": 3})

    connector, http = build(handler)
    notices = connector.fetch()
    assert len(notices) == 3
    # 2 pages suffisent: le total est atteint, pas de 3e appel
    assert len(http.calls) == 2


def test_all_endpoints_down_raises_connector_error():
    connector, _ = build(lambda *a: FakeResponse(status_code=500, payload=None, text="boom"))
    with pytest.raises(ConnectorError) as excinfo:
        connector.fetch()
    assert "Aucun endpoint TED" in str(excinfo.value)


def test_check_reports_working_endpoint():
    def handler(method, url, kwargs):
        if "api.ted.europa.eu" in url:
            return FakeResponse(status_code=404, payload=None, text="not found")
        return FakeResponse(payload={"results": [], "total": 42})

    connector, _ = build(handler)
    ok, message = connector.check()
    assert ok
    assert "v3.0" in message and "42" in message


# --------------------------------------------------------------------------
# Corrections issues du premier test terrain (2026-09-10)
# --------------------------------------------------------------------------
def test_repeated_lot_values_are_deduplicated():
    """TED repete la region pour chaque lot: 'BE332 | BEL | BE332 | BEL'."""
    payload = {**EFORMS_NOTICE,
               "place-of-performance": ["BE332", "BEL", "BE332", "BEL"]}

    def handler(method, url, kwargs):
        page = kwargs["json"].get("page", 1)
        return FakeResponse(payload={"notices": [payload] if page == 1 else []})

    connector, _ = build(handler)
    assert connector.fetch()[0].region == "BE332 | BEL"


def test_estimated_value_and_description_fields_are_requested():
    """Sans ces champs, le budget estime remontait vide sur tous les avis."""
    sent = {}

    def handler(method, url, kwargs):
        sent.update(kwargs["json"])
        return FakeResponse(payload={"notices": []})

    connector, _ = build(handler)
    connector.fetch()
    fields = sent["fields"]
    assert any("estimated-value" in f for f in fields)
    assert any("description" in f for f in fields)


def test_rejected_field_list_falls_back_to_the_minimal_set():
    """Un nom de champ inconnu fait echouer toute la requete: on retente avec
    le socle plutot que de perdre la journee."""
    attempts = []

    def handler(method, url, kwargs):
        fields = kwargs["json"]["fields"]
        attempts.append(fields)
        if any("estimated-value" in f for f in fields):
            return FakeResponse(status_code=400, payload=None,
                                text="unknown field: estimated-value-lot")
        page = kwargs["json"].get("page", 1)
        return FakeResponse(payload={"notices": [EFORMS_NOTICE] if page == 1 else []})

    connector, _ = build(handler)
    notices = connector.fetch()

    assert len(notices) == 1, "le repli doit produire des avis"
    assert len(attempts) >= 2
    assert not any("estimated-value" in f for f in attempts[-1])


def test_persistent_http_400_still_raises():
    connector, _ = build(lambda *a: FakeResponse(status_code=400, payload=None, text="nope"))
    with pytest.raises(ConnectorError, match="Aucun endpoint TED"):
        connector.fetch()
