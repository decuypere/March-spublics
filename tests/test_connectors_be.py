"""Connecteurs belges: flux RSS generique et recherche HTTP pilotee par config."""

from datetime import date

import pytest

from veille_mp.connectors.base import ConnectorError
from veille_mp.connectors.http_search import HttpSearchConnector
from veille_mp.connectors.rss import RssConnector
from tests.fakes import AllowAllRobots, DenyAllRobots, FakeHttpClient, FakeResponse

GLOBAL_CFG = {"filtering": {"cpv_codes": ["71200000"]}}

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Bulletin des Adjudications</title>
  <item>
    <title>Mission d'architecture - renovation de l'hotel de ville</title>
    <link>https://example.invalid/notice/1</link>
    <guid>BDA-2026-001</guid>
    <author>Ville de Namur</author>
    <pubDate>Tue, 08 Sep 2026 09:00:00 +0200</pubDate>
    <description>CPV 71200000. Date limite de reception: 20/10/2026. Montant estime: 850 000 EUR</description>
  </item>
</channel></rss>
"""


def test_rss_entry_is_normalized_with_cpv_and_deadline_extraction():
    http = FakeHttpClient(lambda *a: FakeResponse(content=RSS_XML.encode(), text=RSS_XML))
    spec = {"name": "bda", "type": "rss", "country": "BE",
            "feeds": ["https://example.invalid/rss"]}
    notices = RssConnector(spec, http, AllowAllRobots(), GLOBAL_CFG).fetch()

    assert len(notices) == 1
    notice = notices[0]
    assert notice.source_id == "BDA-2026-001"
    assert notice.country == "BE"
    assert notice.cpv_codes == ["71200000"]
    assert notice.publication_date == date(2026, 9, 8)
    assert notice.deadline == date(2026, 10, 20)
    assert notice.buyer_name == "Ville de Namur"


def test_rss_without_feeds_fails_explicitly():
    http = FakeHttpClient(lambda *a: FakeResponse())
    connector = RssConnector({"name": "bda", "type": "rss", "feeds": []},
                             http, AllowAllRobots(), GLOBAL_CFG)
    with pytest.raises(ConnectorError, match="aucun flux configure"):
        connector.fetch()


def test_one_broken_feed_does_not_lose_the_other():
    def handler(method, url, kwargs):
        if "broken" in url:
            return FakeResponse(status_code=500, payload=None, text="boom")
        return FakeResponse(content=RSS_XML.encode(), text=RSS_XML)

    spec = {"name": "bda", "type": "rss", "country": "BE", "feeds": [
        "https://example.invalid/broken", "https://example.invalid/ok",
    ]}
    notices = RssConnector(spec, FakeHttpClient(handler), AllowAllRobots(), GLOBAL_CFG).fetch()
    assert len(notices) == 1


# --------------------------------------------------------------------------
HTTP_SPEC = {
    "name": "eprocurement_be",
    "type": "http_search",
    "country": "BE",
    "base_url": "https://example.invalid",
    "method": "POST",
    "search_path": "/api/search/notices",
    "request": {"json": {"size": 2, "cpv": "{cpv_csv}", "from": "{date_from}"}},
    "pagination": {"page_param": "page", "start_page": 0, "max_pages": 3},
    "mapping": {
        "items": "data.items",
        "source_id": "id",
        "title": "title",
        "url": "url",
        "buyer_name": "buyer.name",
        "cpv_codes": "cpv[]",
        "publication_date": "publicationDate",
        "deadline": "deadlineDate",
        "description": "description",
        "value_amount": "estimatedValue.amount",
        "value_currency": "estimatedValue.currency",
    },
}

ITEM = {
    "id": "BE-2026-77",
    "title": "<b>Mission d'architecte</b> pour l'ecole",
    "url": "/notices/BE-2026-77",
    "buyer": {"name": "Commune de Uccle"},
    "cpv": ["71200000-0"],
    "publicationDate": "2026-09-08",
    "deadlineDate": "2026-10-20",
    "description": "Mission complete",
    "estimatedValue": {"amount": "425000,50", "currency": "EUR"},
}


def test_http_search_maps_configured_json_paths():
    def handler(method, url, kwargs):
        page = kwargs["json"]["page"]
        items = [ITEM] if page == 0 else []
        return FakeResponse(payload={"data": {"items": items}})

    connector = HttpSearchConnector(HTTP_SPEC, FakeHttpClient(handler),
                                    AllowAllRobots(), GLOBAL_CFG)
    notices = connector.fetch()

    assert len(notices) == 1
    notice = notices[0]
    assert notice.source_id == "BE-2026-77"
    assert notice.title == "Mission d'architecte pour l'ecole"   # HTML retire
    assert notice.url == "https://example.invalid/notices/BE-2026-77"  # URL relative resolue
    assert notice.buyer_name == "Commune de Uccle"
    assert notice.cpv_codes == ["71200000"]
    assert notice.value_amount == 425000.50
    assert notice.value_currency == "EUR"
    assert notice.country == "BE"


def test_request_placeholders_are_rendered():
    seen = {}

    def handler(method, url, kwargs):
        seen.update(kwargs["json"])
        return FakeResponse(payload={"data": {"items": []}})

    HttpSearchConnector(HTTP_SPEC, FakeHttpClient(handler),
                        AllowAllRobots(), GLOBAL_CFG).fetch()
    assert seen["cpv"] == "71200000"
    assert seen["from"].count("-") == 2      # date ISO substituee
    assert seen["page"] == 0


def test_robots_txt_denial_stops_this_source_only():
    connector = HttpSearchConnector(
        HTTP_SPEC, FakeHttpClient(lambda *a: FakeResponse()), DenyAllRobots(), GLOBAL_CFG
    )
    with pytest.raises(ConnectorError, match="robots.txt"):
        connector.fetch()


def test_html_response_produces_an_actionable_error():
    def handler(method, url, kwargs):
        return FakeResponse(status_code=200, payload=None, text="<html>...</html>",
                            headers={"Content-Type": "text/html"})

    connector = HttpSearchConnector(HTTP_SPEC, FakeHttpClient(handler),
                                    AllowAllRobots(), GLOBAL_CFG)
    with pytest.raises(ConnectorError, match="non JSON"):
        connector.fetch()


def test_missing_endpoint_configuration_is_reported():
    spec = {**HTTP_SPEC, "search_path": ""}
    connector = HttpSearchConnector(spec, FakeHttpClient(lambda *a: FakeResponse()),
                                    AllowAllRobots(), GLOBAL_CFG)
    with pytest.raises(ConnectorError, match="search_path"):
        connector.fetch()
