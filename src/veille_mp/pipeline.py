"""Orchestration d'un run de veille.

Principe cle: une source qui echoue est journalisee mais ne bloque ni les
autres sources ni la persistance des donnees deja collectees.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .connectors import build_connector
from .db import Database
from .dedup import Deduplicator
from .filtering import RelevanceFilter
from .http import HttpClient
from .models import Notice, SourceResult
from .robots import RobotsGate

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    run_id: int
    mode: str
    sources: list[SourceResult] = field(default_factory=list)
    fetched: int = 0
    kept: int = 0
    new_notices: int = 0
    duplicates: int = 0
    rejected_examples: list[tuple[str, str]] = field(default_factory=list)
    #: {notice_key: cle de l'avis canonique} - None si l'avis est canonique
    canonical_map: dict[str, str | None] = field(default_factory=dict)
    kept_notices: list[Notice] = field(default_factory=list)

    @property
    def sources_ok(self) -> int:
        return sum(1 for s in self.sources if s.ok)

    @property
    def sources_ko(self) -> int:
        return sum(1 for s in self.sources if not s.ok)

    def summary(self) -> str:
        lines = [
            f"Run #{self.run_id} ({self.mode})",
            f"  Sources : {self.sources_ok} OK / {self.sources_ko} en echec",
            f"  Collecte: {self.fetched} avis bruts",
            f"  Retenus : {self.kept} apres filtrage",
            f"  Doublons: {self.duplicates}",
            f"  Nouveaux: {self.new_notices}",
        ]
        for source in self.sources:
            status = "OK " if source.ok else "KO "
            detail = f"{source.fetched} avis en {source.duration_seconds:.1f}s"
            if not source.ok:
                detail = source.error[:200]
            lines.append(f"    [{status}] {source.name}: {detail}")
        return "\n".join(lines)


class Pipeline:
    def __init__(self, config: Config, db: Database | None = None):
        self.config = config
        self.http = HttpClient(config.get("http", {}) or {})
        self.robots = RobotsGate(
            self.http,
            enabled=bool(config.get("http.respect_robots_txt", True)),
        )
        self.filter = RelevanceFilter(config.get("filtering", {}) or {})
        self.db = db or Database(config.db_path)
        self.dedup = Deduplicator(config.get("dedup", {}) or {}, self.db)
        self._owns_db = db is None

    def close(self) -> None:
        self.http.close()
        if self._owns_db:
            self.db.close()

    # -- collecte -----------------------------------------------------------
    def collect(self, only: list[str] | None = None) -> list[SourceResult]:
        results: list[SourceResult] = []
        for spec in self.config.enabled_sources(only):
            name = spec.get("name", spec.get("type", "?"))
            started = time.monotonic()
            result = SourceResult(name=name)
            try:
                connector = build_connector(spec, self.http, self.robots, self.config.data)
                notices = connector.fetch()
                result.notices = notices
                result.fetched = len(notices)
                log.info("Source %s: %d avis", name, len(notices))
            except Exception as exc:  # noqa: BLE001 - isolation volontaire
                result.ok = False
                result.error = f"{type(exc).__name__}: {exc}"
                log.error("Source %s en echec: %s", name, result.error)
            finally:
                result.duration_seconds = time.monotonic() - started
                results.append(result)
        return results

    # -- run complet --------------------------------------------------------
    def run(self, mode: str = "daily", only: list[str] | None = None,
            dry_run: bool = False) -> RunReport:
        run_id = self.db.start_run(mode)
        report = RunReport(run_id=run_id, mode=mode)

        source_results = self.collect(only)
        report.sources = source_results

        all_kept: list[Notice] = []
        for result in source_results:
            report.fetched += result.fetched
            kept, rejected = self.filter.apply(result.notices)
            all_kept.extend(kept)
            for notice, verdict in rejected[:3]:
                report.rejected_examples.append((notice.title[:80], verdict.reason))
            self.db.log_source_run(
                run_id, result.name, result.ok, result.fetched, len(kept),
                result.error, result.duration_seconds,
            )

        report.kept = len(all_kept)
        report.kept_notices = all_kept
        canonical_map = self.dedup.resolve(all_kept)
        report.canonical_map = canonical_map
        report.duplicates = sum(1 for v in canonical_map.values() if v)

        if not dry_run:
            for notice in all_kept:
                is_new = self.db.upsert(
                    notice, run_id=run_id, duplicate_of=canonical_map.get(notice.key)
                )
                if is_new and not canonical_map.get(notice.key):
                    report.new_notices += 1
        else:
            known = self.db.known_keys()
            report.new_notices = sum(
                1 for n in all_kept
                if n.key not in known and not canonical_map.get(n.key)
            )

        self.db.finish_run(
            run_id,
            fetched=report.fetched,
            kept=report.kept,
            new_notices=report.new_notices,
            duplicates=report.duplicates,
            sources_ok=report.sources_ok,
            sources_ko=report.sources_ko,
        )
        return report

    # -- diagnostic ---------------------------------------------------------
    def doctor(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        specs = self.config.enabled_sources(only) if only else self.config.sources
        for spec in specs:
            name = spec.get("name", "?")
            entry: dict[str, Any] = {
                "name": name,
                "type": spec.get("type"),
                "enabled": bool(spec.get("enabled")),
            }
            try:
                connector = build_connector(spec, self.http, self.robots, self.config.data)
                ok, message = connector.check()
            except Exception as exc:  # noqa: BLE001
                ok, message = False, f"{type(exc).__name__}: {exc}"
            entry["ok"] = ok
            entry["message"] = message
            checks.append(entry)
        return checks
