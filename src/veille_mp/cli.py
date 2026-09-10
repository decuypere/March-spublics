"""Interface en ligne de commande.

  veille init                 cree config/config.yaml a partir de l'exemple
  veille doctor               verifie la disponibilite de chaque source
  veille test                 veille ponctuelle immediate (sans ecriture en base)
  veille run                  veille complete: collecte + stockage + digest
  veille list                 affiche les avis stockes
  veille web                  lance l'interface de consultation
  veille daemon               execution quotidienne interne (alternative a cron)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import (DEFAULT_CONFIG_PATH, EXAMPLE_CONFIG_PATH, Config,
                     ConfigError, load_config)
from .db import Database, rows_to_dicts
from .logging_setup import setup_logging
from .notify import build_digest, send_digest_email, write_digests
from .pipeline import Pipeline

log = logging.getLogger("veille")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _bootstrap(args: argparse.Namespace) -> Config:
    config = load_config(getattr(args, "config", None))
    setup_logging(
        level=getattr(args, "log_level", None) or config.get("logging.level", "INFO"),
        file_path=config.resolve_path("logging.file", "data/logs/veille.log"),
    )
    return config


def _sources_arg(args: argparse.Namespace) -> list[str] | None:
    raw = getattr(args, "source", None)
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("Aucun avis.")
        return
    for row in rows:
        deadline = row.get("deadline") or "n/c"
        value = f"{row['value_amount']:,.0f} {row.get('value_currency','')}".replace(",", " ") \
            if row.get("value_amount") else "n/c"
        print(f"\n  {row.get('title') or '(sans titre)'}")
        print(f"    Acheteur   : {row.get('buyer_name') or 'n/c'}")
        print(f"    Pays/region: {row.get('country') or '?'} {row.get('region') or ''}".rstrip())
        print(f"    CPV        : {row.get('cpv_codes') or 'n/c'}")
        print(f"    Publie le  : {row.get('publication_date') or 'n/c'}   Limite: {deadline}")
        print(f"    Budget     : {value}")
        print(f"    Source     : {row.get('source')} (score {float(row.get('score') or 0):.2f})")
        if row.get("url"):
            print(f"    Lien       : {row['url']}")


# --------------------------------------------------------------------------
# commandes
# --------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    if target.exists() and not args.force:
        print(f"{target} existe deja (utilisez --force pour ecraser).")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE_CONFIG_PATH, target)
    print(f"Configuration creee: {target}")
    print("Editez les codes CPV, les mots-cles et les sources, puis lancez `veille doctor`.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _bootstrap(args)
    pipeline = Pipeline(config)
    try:
        checks = pipeline.doctor(_sources_arg(args))
    finally:
        pipeline.close()

    print("\nDiagnostic des sources")
    print("=" * 72)
    failures = 0
    for check in checks:
        state = "OK " if check["ok"] else "KO "
        flag = "actif " if check["enabled"] else "inactif"
        print(f"  [{state}] {check['name']:<18} type={check['type']:<12} ({flag})")
        print(f"          {check['message']}")
        if check["enabled"] and not check["ok"]:
            failures += 1
    print("=" * 72)
    print(f"Base de donnees : {config.db_path}")
    print(f"Configuration   : {config.path}")
    if failures:
        print(f"\n{failures} source(s) active(s) en echec. La veille continuera "
              "malgre tout avec les sources disponibles.")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Veille ponctuelle immediate, pour valider le filtrage."""
    config = _bootstrap(args)
    if args.lookback:
        for spec in config.sources:
            spec["lookback_days"] = args.lookback

    pipeline = Pipeline(config)
    try:
        report = pipeline.run(
            mode="test",
            only=_sources_arg(args),
            dry_run=not args.persist,
        )
        print("\n" + report.summary())

        if report.rejected_examples:
            print("\nExemples d'avis ecartes (verification du filtrage):")
            for title, reason in report.rejected_examples[:10]:
                print(f"  - {title} -> {reason}")

        kept: list[dict] = []
        for notice in report.kept_notices:
            data = notice.to_dict()
            data["cpv_codes"] = ",".join(notice.cpv_codes)
            data["duplicate_of"] = report.canonical_map.get(notice.key)
            kept.append(data)
        kept.sort(key=lambda r: (r.get("score") or 0), reverse=True)

        canonical = [r for r in kept if not r.get("duplicate_of")]
        print(f"\nAvis retenus ({len(canonical)} uniques, "
              f"{len(kept) - len(canonical)} doublon(s) ecarte(s)):")
        _print_table(canonical[: args.limit])

        duplicates = [r for r in kept if r.get("duplicate_of")]
        if duplicates:
            print("\nDoublons detectes (rattaches a un avis deja retenu):")
            for row in duplicates[:10]:
                print(f"  - [{row['source']}] {row['title'][:70]} -> {row['duplicate_of']}")

        if args.json:
            Path(args.json).write_text(
                json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\nResultats ecrits dans {args.json}")

        if not args.persist:
            print("\n(mode test: rien n'a ete ecrit dans la base; "
                  "ajoutez --persist pour enregistrer)")
        return 0 if report.sources_ko == 0 else 2
    finally:
        pipeline.close()


def cmd_run(args: argparse.Namespace) -> int:
    config = _bootstrap(args)
    pipeline = Pipeline(config)
    try:
        report = pipeline.run(mode=args.mode, only=_sources_arg(args))
        log.info("\n%s", report.summary())

        notify_cfg = config.get("notify", {}) or {}
        new_only = bool(notify_cfg.get("new_only", True))
        rows = rows_to_dicts(
            pipeline.db.new_notices_for_run(report.run_id) if new_only
            else pipeline.db.query_notices(limit=500, order="publication_date")
        )
        errors = [f"{s.name}: {s.error}" for s in report.sources if not s.ok]
        digest = build_digest(rows, run_date=date.today(), errors=errors)

        written = write_digests(
            digest,
            config.resolve_path("notify.output_dir", "data/digests"),
            list(notify_cfg.get("formats") or ["html", "csv", "md"]),
        )
        for fmt, path in written.items():
            print(f"Digest {fmt}: {path}")

        try:
            sent = send_digest_email(
                notify_cfg.get("email", {}) or {}, digest, len(rows),
                run_date=date.today(), attachments=written,
            )
            if sent:
                print("Email envoye.")
        except Exception as exc:  # noqa: BLE001 - l'envoi ne doit pas casser le run
            log.error("Echec de l'envoi de l'email: %s", exc)

        print(f"\n{len(rows)} avis dans le digest, {report.new_notices} nouveaute(s).")
        return 0 if report.sources_ko == 0 else 2
    finally:
        pipeline.close()


def cmd_list(args: argparse.Namespace) -> int:
    config = _bootstrap(args)
    with Database(config.db_path) as db:
        new_since = None
        if args.new_days:
            new_since = (date.today() - timedelta(days=args.new_days)).isoformat()
        rows = rows_to_dicts(db.query_notices(
            limit=args.limit,
            source=args.source,
            country=args.country,
            cpv=args.cpv,
            text=args.query,
            new_since=new_since,
            open_only=args.open_only,
            order=args.order,
        ))
        stats = db.stats()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"\n{stats['total']} avis en base ({stats['open']} encore ouverts, "
          f"{stats['duplicates']} doublons ecartes)")
    _print_table(rows)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = _bootstrap(args)
    with Database(config.db_path) as db:
        stats = db.stats()
        runs = rows_to_dicts(db.recent_runs(limit=10))
    print(json.dumps({"stats": stats, "recent_runs": runs}, ensure_ascii=False, indent=2))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    config = _bootstrap(args)
    from .web import create_app

    app = create_app(config)
    host = args.host or config.get("web.host", "127.0.0.1")
    port = args.port or int(config.get("web.port", 8080))
    print(f"Interface disponible sur http://{host}:{port}")
    app.run(host=host, port=port, debug=args.debug)
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Boucle interne: execute la veille une fois par jour a l'heure prevue.

    Alternative a cron quand aucun planificateur systeme n'est disponible.
    Voir scripts/ pour les exemples cron / systemd (recommandes en production).
    """
    config = _bootstrap(args)
    at = str(args.at or config.get("schedule.daily_at", "07:00"))
    hour, _, minute = at.partition(":")
    hour, minute = int(hour), int(minute or 0)
    log.info("Daemon demarre: execution quotidienne a %02d:%02d (heure locale)", hour, minute)

    if args.run_now:
        cmd_run(argparse.Namespace(**{**vars(args), "mode": "daily"}))

    while True:
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        sleep_seconds = (next_run - now).total_seconds()
        log.info("Prochaine execution: %s (dans %.0f min)", next_run, sleep_seconds / 60)
        time.sleep(sleep_seconds)
        try:
            cmd_run(argparse.Namespace(**{**vars(args), "mode": "daily"}))
        except Exception as exc:  # noqa: BLE001 - le daemon ne doit jamais mourir
            log.exception("Echec du run quotidien: %s", exc)
        time.sleep(60)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veille",
        description="Veille quotidienne sur les marches publics d'architecture (Belgique + Europe)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-c", "--config", help="chemin du fichier de configuration")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="cree config/config.yaml")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="verifie l'acces a chaque source")
    p_doctor.add_argument("-s", "--source", help="sources a tester (separees par des virgules)")
    p_doctor.set_defaults(func=cmd_doctor)

    p_test = sub.add_parser("test", help="veille ponctuelle immediate (mode test)")
    p_test.add_argument("-s", "--source", help="sources a interroger (defaut: sources activees)")
    p_test.add_argument("-n", "--limit", type=int, default=20, help="avis affiches")
    p_test.add_argument("--lookback", type=int, help="fenetre en jours (surcharge la config)")
    p_test.add_argument("--persist", action="store_true", help="ecrire aussi en base")
    p_test.add_argument("--json", help="ecrit les resultats dans ce fichier JSON")
    p_test.set_defaults(func=cmd_test)

    p_run = sub.add_parser("run", help="veille complete + stockage + digest")
    p_run.add_argument("-s", "--source", help="restreindre a certaines sources")
    p_run.add_argument("--mode", default="daily", help="etiquette du run (daily, manual, ...)")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="affiche les avis stockes")
    p_list.add_argument("-n", "--limit", type=int, default=25)
    p_list.add_argument("-s", "--source")
    p_list.add_argument("--country")
    p_list.add_argument("--cpv")
    p_list.add_argument("-q", "--query", help="recherche texte")
    p_list.add_argument("--new-days", type=int, dest="new_days",
                        help="uniquement les avis detectes depuis N jours")
    p_list.add_argument("--open-only", action="store_true", dest="open_only",
                        help="uniquement les avis dont la date limite n'est pas passee")
    p_list.add_argument("--order", default="publication_date",
                        choices=["publication_date", "deadline", "score", "first_seen"])
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_stats = sub.add_parser("stats", help="statistiques et derniers runs (JSON)")
    p_stats.set_defaults(func=cmd_stats)

    p_web = sub.add_parser("web", help="interface web de consultation")
    p_web.add_argument("--host")
    p_web.add_argument("--port", type=int)
    p_web.add_argument("--debug", action="store_true")
    p_web.set_defaults(func=cmd_web)

    p_daemon = sub.add_parser("daemon", help="execution quotidienne interne")
    p_daemon.add_argument("--at", help="heure locale HH:MM")
    p_daemon.add_argument("--run-now", action="store_true", dest="run_now",
                          help="executer immediatement au demarrage")
    p_daemon.add_argument("-s", "--source")
    p_daemon.set_defaults(func=cmd_daemon)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"Erreur de configuration: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
