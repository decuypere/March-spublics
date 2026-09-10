from datetime import date

from veille_mp.config import Config, load_config
from veille_mp.notify.digest import build_digest, write_digests

ROWS = [{
    "title": "Mission d'architecture & renovation <ecole>",
    "buyer_name": "Commune d'Ixelles",
    "country": "BE", "region": "BE100",
    "cpv_codes": "71200000,71221000",
    "publication_date": "2026-09-08", "deadline": "2026-10-20",
    "value_amount": 850000.0, "value_currency": "EUR",
    "source": "ted", "score": 1.0,
    "url": "https://ted.europa.eu/fr/notice/-/detail/1",
}]


def test_digest_contains_every_required_field():
    digest = build_digest(ROWS, run_date=date(2026, 9, 10))
    for fmt in ("html", "csv", "md"):
        content = digest[fmt]
        assert "Ixelles" in content
        assert "71200000" in content
        assert "2026-10-20" in content
        assert "ted.europa.eu" in content
    assert "850 000" in digest["html"]


def test_html_is_escaped():
    digest = build_digest(ROWS)
    assert "<ecole>" not in digest["html"]
    assert "&lt;ecole&gt;" in digest["html"]


def test_empty_digest_is_still_valid():
    digest = build_digest([], run_date=date(2026, 9, 10))
    assert "Aucun nouveau marche" in digest["html"]
    assert digest["csv"].startswith("Titre;")


def test_source_errors_are_surfaced_in_the_digest():
    digest = build_digest([], errors=["eprocurement_be: HTTP 503"])
    assert "eprocurement_be" in digest["html"]
    assert "eprocurement_be" in digest["md"]


def test_write_digests_creates_the_requested_formats(tmp_path):
    digest = build_digest(ROWS, run_date=date(2026, 9, 10))
    written = write_digests(digest, tmp_path, ["html", "csv"], run_date=date(2026, 9, 10))
    assert set(written) == {"html", "csv"}
    assert written["html"].name == "digest-2026-09-10.html"
    assert written["csv"].read_text(encoding="utf-8").count("\n") == 2


def test_example_config_is_loadable_and_has_architecture_cpv():
    config = load_config()
    cpv = config.get("filtering.cpv_codes")
    assert "71200000" in cpv and "71220000" in cpv
    assert config.get("filtering.min_score") is not None
    assert any(s["type"] == "ted" for s in config.sources)


def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("VEILLE_FILTERING__MIN_SCORE", "0.9")
    monkeypatch.setenv("VEILLE_NOTIFY__EMAIL__RECIPIENTS", "a@example.com,b@example.com")
    config = load_config()
    assert config.get("filtering.min_score") == 0.9
    assert config.get("notify.email.recipients") == ["a@example.com", "b@example.com"]


def test_dotted_access_and_enabled_sources():
    config = Config({"sources": [
        {"name": "a", "type": "sample", "enabled": True},
        {"name": "b", "type": "sample", "enabled": False},
    ]})
    assert [s["name"] for s in config.enabled_sources()] == ["a"]
    # une source explicitement demandee est utilisee meme si desactivee
    assert [s["name"] for s in config.enabled_sources(["b"])] == ["b"]
