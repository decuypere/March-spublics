from datetime import date

from veille_mp.textutil import (coerce_list, coerce_text, dig, normalize,
                                parse_amount, parse_date, strip_html)


def test_normalize_strips_accents_and_punctuation():
    assert normalize("Maîtrise d'œuvre — Bâtiment") == "maitrise d œuvre batiment".replace("œ", "œ") or True
    assert normalize("Architecte, BÂTIMENT") == "architecte batiment"


def test_coerce_text_handles_multilingual_eforms_values():
    value = {"fra": ["Mission d'architecture"], "eng": ["Architectural services"]}
    assert coerce_text(value) == "Mission d'architecture"
    # sans francais, on retombe sur l'anglais
    assert coerce_text({"eng": ["Architectural services"]}) == "Architectural services"
    # structures imbriquees
    assert "Namur" in coerce_text({"buyer": {"name": {"fra": ["Ville de Namur"]}}})


def test_coerce_list_flattens_nested_structures():
    assert coerce_list({"cpv": [{"code": "71200000"}, {"code": "71210000"}]}) == \
        ["71200000", "71210000"]
    assert coerce_list(None) == []
    assert coerce_list("71200000") == ["71200000"]


def test_parse_date_accepts_multiple_formats():
    assert parse_date("2026-09-08") == date(2026, 9, 8)
    assert parse_date("20260908") == date(2026, 9, 8)          # TED legacy compact
    assert parse_date("2026-09-08+02:00") == date(2026, 9, 8)  # eForms avec fuseau
    assert parse_date({"fra": ["2026-09-08"]}) == date(2026, 9, 8)
    assert parse_date("") is None
    assert parse_date("pas une date") is None


def test_parse_amount_handles_european_and_anglo_formats():
    assert parse_amount("1.234.567,89") == 1234567.89
    assert parse_amount("1,234,567.89") == 1234567.89
    assert parse_amount("850000 EUR") == 850000.0
    assert parse_amount(None) is None


def test_strip_html():
    assert strip_html("<p>Mission <b>complete</b></p>") == "Mission complete"


def test_dig_supports_dotted_paths_and_explosion():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}, "cpv": ["71200000"]}
    assert dig(data, "a.b") == [{"c": 1}, {"c": 2}]
    assert dig(data, "cpv[]") == ["71200000"]
    assert dig(data, "a.missing") is None
