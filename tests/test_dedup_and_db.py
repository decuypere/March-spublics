from datetime import date

from veille_mp.db import Database
from veille_mp.dedup import Deduplicator
from veille_mp.models import Notice

DEDUP_CFG = {"source_priority": ["ted", "enot_rss"], "title_similarity": 0.92}


def make(source, source_id, title, buyer="Commune d'Ixelles", deadline=date(2026, 10, 20)):
    return Notice(source=source, source_id=source_id, title=title,
                  buyer_name=buyer, deadline=deadline)


def test_same_market_from_two_sources_is_deduplicated():
    a = make("enot_rss", "1", "Mission complete d'auteur de projet - ecole")
    b = make("ted", "00512345-2026", "Mission complete d'auteur de projet - ecole")
    mapping = Deduplicator(DEDUP_CFG).resolve([a, b])
    # TED est prioritaire: il reste canonique, l'autre pointe vers lui
    assert mapping[b.key] is None
    assert mapping[a.key] == b.key


def test_distinct_markets_are_not_merged():
    a = make("ted", "1", "Construction d'une ecole")
    b = make("ted", "2", "Renovation d'une piscine", buyer="Ville de Namur")
    mapping = Deduplicator(DEDUP_CFG).resolve([a, b])
    assert mapping == {a.key: None, b.key: None}


def test_fuzzy_title_match_within_same_buyer():
    a = make("ted", "1", "Mission complete d'architecture pour l'ecole communale",
             deadline=date(2026, 10, 20))
    b = make("enot_rss", "2", "Mission complete d'architecture pour l ecole communale",
             deadline=date(2026, 10, 21))  # empreinte differente (echeance)
    mapping = Deduplicator(DEDUP_CFG).resolve([a, b])
    assert mapping[b.key] == a.key


def test_resolution_is_stable_regardless_of_input_order():
    a = make("enot_rss", "1", "Mission ecole")
    b = make("ted", "2", "Mission ecole")
    first = Deduplicator(DEDUP_CFG).resolve([a, b])
    second = Deduplicator(DEDUP_CFG).resolve([b, a])
    assert first == second


def test_dedup_against_history_in_database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    stored = make("ted", "00512345-2026", "Mission complete - ecole")
    db.upsert(stored, run_id=1)

    fresh = make("enot_rss", "42", "Mission complete - ecole")
    mapping = Deduplicator(DEDUP_CFG, db).resolve([fresh])
    assert mapping[fresh.key] == stored.key
    db.close()


def test_upsert_reports_new_then_update(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    notice = make("ted", "1", "Mission ecole")
    assert db.upsert(notice, run_id=1) is True     # nouveaute
    assert db.upsert(notice, run_id=2) is False    # deja connu
    assert db.stats()["total"] == 1
    db.close()


def test_query_filters_and_open_only(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.upsert(Notice(source="ted", source_id="1", title="Ecole", country="BE",
                     cpv_codes=["71200000"], deadline=date(2020, 1, 1)), run_id=1)
    db.upsert(Notice(source="ted", source_id="2", title="Piscine", country="FR",
                     cpv_codes=["71220000"], deadline=date(2099, 1, 1)), run_id=1)

    assert len(db.query_notices(country="be")) == 1
    assert len(db.query_notices(cpv="71220000")) == 1
    assert len(db.query_notices(text="pisc")) == 1
    assert len(db.query_notices(open_only=True)) == 1
    assert len(db.new_notices_for_run(1)) == 2
    db.close()


def test_duplicates_are_excluded_from_default_queries(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    canonical = make("ted", "1", "Mission ecole")
    duplicate = make("enot_rss", "2", "Mission ecole")
    db.upsert(canonical, run_id=1)
    db.upsert(duplicate, run_id=1, duplicate_of=canonical.key)

    assert len(db.query_notices()) == 1
    assert len(db.query_notices(include_duplicates=True)) == 2
    assert db.stats()["duplicates"] == 1
    db.close()


# --------------------------------------------------------------------------
# Corrections issues du premier test terrain (2026-09-10)
# --------------------------------------------------------------------------
def test_two_distinct_notices_from_the_same_source_are_never_merged():
    """Un numero de publication TED est unique: deux avis TED aux titres
    generiques identiques restent deux marches distincts."""
    generique = "Allemagne - Services d'architecture, d'ingenierie et de planification"
    a = make("ted", "612558-2026", generique, buyer="", deadline=None)
    b = make("ted", "623715-2026", generique, buyer="", deadline=None)
    mapping = Deduplicator(DEDUP_CFG).resolve([a, b])
    assert mapping == {a.key: None, b.key: None}


def test_weak_fingerprint_does_not_merge_across_sources_either():
    """Titre generique, ni acheteur ni echeance: aucune preuve de doublon."""
    a = make("ted", "1", "Services d'architecture", buyer="", deadline=None)
    b = make("enot_rss", "2", "Services d'architecture", buyer="", deadline=None)
    mapping = Deduplicator(DEDUP_CFG).resolve([a, b])
    assert mapping[a.key] is None and mapping[b.key] is None


def test_strong_fingerprint_still_merges_across_sources():
    """Le garde-fou ne doit pas casser la deduplication legitime."""
    titre = "Mission complete d'auteur de projet pour une ecole fondamentale"
    a = make("ted", "1", titre, buyer="Commune d'Ixelles", deadline=date(2026, 10, 20))
    b = make("enot_rss", "2", titre, buyer="Commune d'Ixelles", deadline=date(2026, 10, 20))
    mapping = Deduplicator(DEDUP_CFG).resolve([a, b])
    assert mapping[b.key] == a.key


def test_stored_canonical_stays_the_reference_for_later_runs(tmp_path):
    """Empreinte forte: un rectificatif TED et le meme avis vu ailleurs
    pointent tous deux vers l'avis deja enregistre."""
    db = Database(tmp_path / "test.sqlite3")
    titre = "Mission complete d'auteur de projet pour une ecole"
    stored = make("ted", "612558-2026", titre, deadline=date(2026, 10, 20))
    db.upsert(stored, run_id=1)

    rectificatif = make("ted", "623715-2026", titre, deadline=date(2026, 10, 20))
    autre_source = make("enot_rss", "9", titre, deadline=date(2026, 10, 20))
    mapping = Deduplicator(DEDUP_CFG, db).resolve([rectificatif, autre_source])

    assert mapping[rectificatif.key] == stored.key
    assert mapping[autre_source.key] == stored.key
    db.close()
