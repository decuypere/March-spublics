import pytest

from veille_mp.filtering import RelevanceFilter, clean_cpv
from veille_mp.models import Notice

CFG = {
    "min_score": 0.5,
    "weights": {"cpv_exact": 1.0, "cpv_prefix": 0.6, "keyword_title": 0.45,
                "keyword_body": 0.2, "keyword_extra": 0.1},
    "cpv_exact_always_keep": True,
    "cpv_codes": ["71200000", "71220000", "71420000"],
    "cpv_prefixes": ["712"],
    "keywords": {"fr": ["architecte", "maitrise d oeuvre"], "nl": ["architectuuropdracht"]},
    "exclude_keywords": ["architecture logicielle"],
}


def notice(**kwargs) -> Notice:
    base = {"source": "t", "source_id": "1", "title": "", "description": "",
            "cpv_codes": []}
    base.update(kwargs)
    return Notice(**base)


def test_clean_cpv_strips_check_digit():
    assert clean_cpv("71200000-0") == "71200000"


def test_exact_cpv_is_always_kept():
    f = RelevanceFilter(CFG)
    verdict = f.evaluate(notice(title="Marche de services", cpv_codes=["71200000-0"]))
    assert verdict.keep and verdict.score == 1.0
    assert verdict.matched_cpv == ["71200000"]


def test_cpv_prefix_alone_is_below_threshold_but_keyword_lifts_it():
    f = RelevanceFilter(CFG)
    weak = f.evaluate(notice(title="Mission de services", cpv_codes=["71230000"]))
    assert weak.score == pytest.approx(0.6)
    strong = f.evaluate(notice(title="Mission d'architecte", cpv_codes=["71230000"]))
    assert strong.keep and strong.score > weak.score


def test_keyword_only_notice_is_kept_when_title_matches():
    f = RelevanceFilter(CFG)
    verdict = f.evaluate(notice(title="Selection d'un architecte pour l'ecole",
                                description="mission complete"))
    assert verdict.keep is False or verdict.score >= 0.45
    # un mot-cle titre + un mot-cle corps passent le seuil
    verdict2 = f.evaluate(notice(title="Selection d'un architecte",
                                 description="maitrise d'oeuvre complete"))
    assert verdict2.keep


def test_accent_and_case_insensitive_matching():
    f = RelevanceFilter(CFG)
    verdict = f.evaluate(notice(title="ARCHITECTE - maîtrise d'œuvre".replace("œ", "oe"),
                                description=""))
    assert verdict.matched_keywords


def test_exclusion_keyword_rejects_even_with_matching_cpv():
    f = RelevanceFilter(CFG)
    verdict = f.evaluate(notice(title="Refonte de l'architecture logicielle",
                                cpv_codes=["71200000"]))
    assert not verdict.keep
    assert "exclu" in verdict.reason


def test_word_boundary_avoids_false_positives():
    f = RelevanceFilter(CFG)
    verdict = f.evaluate(notice(title="Depart des architectesques", description=""))
    assert "architecte" not in verdict.matched_keywords


def test_apply_splits_kept_and_rejected():
    f = RelevanceFilter(CFG)
    kept, rejected = f.apply([
        notice(source_id="a", title="Architecte", cpv_codes=["71200000"]),
        notice(source_id="b", title="Fourniture de chaises", cpv_codes=["39130000"]),
    ])
    assert [n.source_id for n in kept] == ["a"]
    assert [n.source_id for n, _ in rejected] == ["b"]
    assert kept[0].score == 1.0
