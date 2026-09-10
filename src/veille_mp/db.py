"""Persistance SQLite: historique des avis, doublons, journal des runs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import Notice
from .textutil import utcnow_iso

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS notices (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_key        TEXT NOT NULL UNIQUE,
    source            TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    dedup_key         TEXT NOT NULL,
    duplicate_of      TEXT,               -- notice_key de l'avis canonique
    title             TEXT,
    url               TEXT,
    buyer_name        TEXT,
    country           TEXT,
    region            TEXT,
    cpv_codes         TEXT,
    publication_date  TEXT,
    deadline          TEXT,
    value_amount      REAL,
    value_currency    TEXT,
    notice_type       TEXT,
    description       TEXT,
    language          TEXT,
    score             REAL DEFAULT 0,
    matched_cpv       TEXT,
    matched_keywords  TEXT,
    raw_json          TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    run_id            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_notices_dedup ON notices(dedup_key);
CREATE INDEX IF NOT EXISTS idx_notices_pub ON notices(publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_first_seen ON notices(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_notices_source ON notices(source);
CREATE INDEX IF NOT EXISTS idx_notices_dupof ON notices(duplicate_of);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    mode          TEXT,
    fetched       INTEGER DEFAULT 0,
    kept          INTEGER DEFAULT 0,
    new_notices   INTEGER DEFAULT 0,
    duplicates    INTEGER DEFAULT 0,
    sources_ok    INTEGER DEFAULT 0,
    sources_ko    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS source_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    source      TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    fetched     INTEGER DEFAULT 0,
    kept        INTEGER DEFAULT 0,
    error       TEXT,
    duration_s  REAL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_runs_run ON source_runs(run_id);
"""

NOTICE_COLUMNS = [
    "source", "source_id", "notice_key", "dedup_key", "title", "url", "buyer_name",
    "country", "region", "cpv_codes", "publication_date", "deadline", "value_amount",
    "value_currency", "notice_type", "description", "language", "score",
    "matched_cpv", "matched_keywords", "raw_json",
]


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- runs -------------------------------------------------------------
    def start_run(self, mode: str) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, mode) VALUES (?, ?)", (utcnow_iso(), mode)
            )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, **counts: Any) -> None:
        fields = ", ".join(f"{k} = ?" for k in counts)
        values = list(counts.values())
        with self.tx() as conn:
            conn.execute(
                f"UPDATE runs SET finished_at = ?{', ' + fields if fields else ''} WHERE id = ?",
                [utcnow_iso(), *values, run_id],
            )

    def log_source_run(self, run_id: int, source: str, ok: bool, fetched: int,
                       kept: int, error: str, duration_s: float) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO source_runs (run_id, source, ok, fetched, kept, error, duration_s, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, source, 1 if ok else 0, fetched, kept, error[:2000], duration_s, utcnow_iso()),
            )

    # -- notices ----------------------------------------------------------
    def known_keys(self) -> set[str]:
        rows = self._conn.execute("SELECT notice_key FROM notices").fetchall()
        return {r["notice_key"] for r in rows}

    def canonical_for_dedup_key(self, dedup_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT notice_key FROM notices WHERE dedup_key = ? AND duplicate_of IS NULL"
            " ORDER BY first_seen_at LIMIT 1",
            (dedup_key,),
        ).fetchone()
        return row["notice_key"] if row else None

    def upsert(self, notice: Notice, run_id: int | None = None,
               duplicate_of: str | None = None) -> bool:
        """Insere ou met a jour un avis. Retourne True si c'est une nouveaute."""
        row = notice.to_row()
        now = utcnow_iso()
        existing = self._conn.execute(
            "SELECT id FROM notices WHERE notice_key = ?", (row["notice_key"],)
        ).fetchone()

        if existing:
            sets = ", ".join(f"{c} = :{c}" for c in NOTICE_COLUMNS)
            params = dict(row)
            params.update({"last_seen_at": now, "duplicate_of": duplicate_of})
            with self.tx() as conn:
                conn.execute(
                    f"UPDATE notices SET {sets}, last_seen_at = :last_seen_at,"
                    f" duplicate_of = :duplicate_of WHERE notice_key = :notice_key",
                    params,
                )
            return False

        cols = NOTICE_COLUMNS + ["duplicate_of", "first_seen_at", "last_seen_at", "run_id"]
        params = dict(row)
        params.update({
            "duplicate_of": duplicate_of,
            "first_seen_at": now,
            "last_seen_at": now,
            "run_id": run_id,
        })
        placeholders = ", ".join(f":{c}" for c in cols)
        with self.tx() as conn:
            conn.execute(
                f"INSERT INTO notices ({', '.join(cols)}) VALUES ({placeholders})", params
            )
        return True

    # -- lecture ----------------------------------------------------------
    @staticmethod
    def _build_where(
        *,
        source: str | None = None,
        country: str | None = None,
        cpv: str | None = None,
        text: str | None = None,
        new_since: str | None = None,
        run_id: int | None = None,
        open_only: bool = False,
        include_duplicates: bool = False,
    ) -> tuple[str, list[Any]]:
        """Construit la clause WHERE partagee par query_notices et count_notices."""
        where: list[str] = []
        params: list[Any] = []
        if not include_duplicates:
            where.append("duplicate_of IS NULL")
        if source:
            where.append("source = ?")
            params.append(source)
        if country:
            where.append("UPPER(country) = ?")
            params.append(country.upper())
        if cpv:
            where.append("cpv_codes LIKE ?")
            params.append(f"%{cpv}%")
        if text:
            where.append("(LOWER(title) LIKE ? OR LOWER(buyer_name) LIKE ? OR LOWER(description) LIKE ?)")
            needle = f"%{text.lower()}%"
            params.extend([needle, needle, needle])
        if new_since:
            where.append("first_seen_at >= ?")
            params.append(new_since)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if open_only:
            where.append("(deadline IS NULL OR deadline >= date('now'))")

        return (" WHERE " + " AND ".join(where)) if where else "", params

    def query_notices(self, *, limit: int = 200, offset: int = 0,
                      order: str = "publication_date", **filters: Any) -> list[sqlite3.Row]:
        where_sql, params = self._build_where(**filters)
        order_sql = {
            "publication_date": "publication_date DESC NULLS LAST, first_seen_at DESC",
            "deadline": "deadline ASC NULLS LAST",
            "score": "score DESC, publication_date DESC",
            "first_seen": "first_seen_at DESC",
        }.get(order, "publication_date DESC")

        sql = f"SELECT * FROM notices{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?"
        return self._conn.execute(sql, [*params, limit, offset]).fetchall()

    def count_notices(self, **filters: Any) -> int:
        filters.pop("order", None)
        where_sql, params = self._build_where(**filters)
        return int(self._conn.execute(
            f"SELECT COUNT(*) c FROM notices{where_sql}", params
        ).fetchone()["c"])

    def new_notices_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM notices WHERE run_id = ? AND duplicate_of IS NULL"
            " ORDER BY score DESC, publication_date DESC",
            (run_id,),
        ).fetchall()

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def source_runs(self, run_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM source_runs WHERE run_id = ? ORDER BY source", (run_id,)
        ).fetchall()

    def stats(self) -> dict[str, Any]:
        total = self._conn.execute(
            "SELECT COUNT(*) c FROM notices WHERE duplicate_of IS NULL"
        ).fetchone()["c"]
        dupes = self._conn.execute(
            "SELECT COUNT(*) c FROM notices WHERE duplicate_of IS NOT NULL"
        ).fetchone()["c"]
        by_source = self._conn.execute(
            "SELECT source, COUNT(*) c FROM notices GROUP BY source ORDER BY c DESC"
        ).fetchall()
        open_now = self._conn.execute(
            "SELECT COUNT(*) c FROM notices WHERE duplicate_of IS NULL"
            " AND (deadline IS NULL OR deadline >= date('now'))"
        ).fetchone()["c"]
        return {
            "total": total,
            "duplicates": dupes,
            "open": open_now,
            "by_source": {r["source"]: r["c"] for r in by_source},
        }


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
