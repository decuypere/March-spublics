"""Une source indisponible doit etre journalisee sans bloquer les autres
ni faire perdre les donnees deja collectees (exigence 7 du cahier des charges)."""

from veille_mp.config import Config
from veille_mp.db import Database
from veille_mp.pipeline import Pipeline

BASE_CFG = {
    "database": {"path": "unused"},
    "http": {"requests_per_second": 0, "respect_robots_txt": False},
    "filtering": {
        "min_score": 0.5,
        "cpv_codes": ["71200000", "71220000", "71420000"],
        "cpv_prefixes": ["712"],
        "keywords": {"fr": ["architecte", "architecture", "conception"],
                     "nl": ["architectuuropdracht", "bouwkundig ontwerp"]},
        "exclude_keywords": ["architecture logicielle"],
    },
    "dedup": {"source_priority": ["sample", "broken"], "title_similarity": 0.92},
    "sources": [
        {"name": "sample", "type": "sample", "enabled": True},
        # 'feeds' vide -> le connecteur RSS leve une erreur exploitee ci-dessous
        {"name": "broken", "type": "rss", "enabled": True, "feeds": []},
    ],
}


def make_pipeline(tmp_path):
    config = Config(BASE_CFG)
    db = Database(tmp_path / "test.sqlite3")
    return Pipeline(config, db), db


def test_failing_source_does_not_block_the_others(tmp_path):
    pipeline, db = make_pipeline(tmp_path)
    try:
        report = pipeline.run(mode="test")
    finally:
        pipeline.close()

    assert report.sources_ok == 1
    assert report.sources_ko == 1
    assert report.new_notices > 0, "les avis de la source saine doivent etre conserves"

    failing = next(s for s in report.sources if s.name == "broken")
    assert not failing.ok and "feeds" in failing.error


def test_source_failures_are_persisted_in_the_run_log(tmp_path):
    pipeline, db = make_pipeline(tmp_path)
    try:
        report = pipeline.run(mode="test")
        logged = {row["source"]: row for row in db.source_runs(report.run_id)}
    finally:
        pipeline.close()

    assert logged["sample"]["ok"] == 1
    assert logged["broken"]["ok"] == 0
    assert logged["broken"]["error"]

    run = [r for r in db.recent_runs() if r["id"] == report.run_id][0]
    assert run["sources_ko"] == 1
    assert run["finished_at"] is not None


def test_second_run_reports_no_new_notices(tmp_path):
    pipeline, db = make_pipeline(tmp_path)
    try:
        first = pipeline.run(mode="test")
        second = pipeline.run(mode="test")
    finally:
        pipeline.close()

    assert first.new_notices > 0
    assert second.new_notices == 0, "l'historique doit eviter de re-signaler les memes avis"
    assert second.kept == first.kept


def test_dry_run_leaves_the_database_untouched(tmp_path):
    pipeline, db = make_pipeline(tmp_path)
    try:
        report = pipeline.run(mode="test", dry_run=True)
    finally:
        pipeline.close()

    assert report.new_notices > 0        # comptabilise...
    assert db.stats()["total"] == 0      # ...mais rien n'est ecrit


def test_unknown_source_type_is_reported_as_a_source_failure(tmp_path):
    config = Config({**BASE_CFG, "sources": [
        {"name": "sample", "type": "sample", "enabled": True},
        {"name": "mystere", "type": "inexistant", "enabled": True},
    ]})
    db = Database(tmp_path / "test.sqlite3")
    pipeline = Pipeline(config, db)
    try:
        report = pipeline.run(mode="test")
    finally:
        pipeline.close()

    assert report.sources_ok == 1 and report.sources_ko == 1
    assert "Type de source inconnu" in next(s for s in report.sources if not s.ok).error
