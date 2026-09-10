"""Interface web legere de consultation (Flask)."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from flask import Flask, Response, render_template, request

from ..config import Config, load_config
from ..db import Database, rows_to_dicts

PAGE_SIZE = 50


def create_app(config: Config | None = None) -> Flask:
    config = config or load_config()
    app = Flask(__name__)
    app.config["VEILLE_CONFIG"] = config

    def open_db() -> Database:
        return Database(config.db_path)

    def read_filters() -> dict:
        args = request.args
        new_days = args.get("new_days", type=int)
        filters = {
            "source": args.get("source") or None,
            "country": args.get("country") or None,
            "cpv": args.get("cpv") or None,
            "text": args.get("q") or None,
            "open_only": args.get("open_only") == "1",
            "order": args.get("order") or "publication_date",
            "new_since": (
                (date.today() - timedelta(days=new_days)).isoformat() if new_days else None
            ),
        }
        return filters

    @app.route("/")
    def index() -> str:
        page = max(1, request.args.get("page", 1, type=int))
        filters = read_filters()
        with open_db() as db:
            rows = rows_to_dicts(
                db.query_notices(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, **filters)
            )
            total = db.count_notices(**filters)
            stats = db.stats()
            runs = rows_to_dicts(db.recent_runs(limit=5))
        return render_template(
            "index.html",
            rows=rows,
            total=total,
            page=page,
            page_size=PAGE_SIZE,
            pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            stats=stats,
            runs=runs,
            args=request.args,
            today=date.today().isoformat(),
        )

    @app.route("/export.csv")
    def export_csv() -> Response:
        filters = read_filters()
        with open_db() as db:
            rows = rows_to_dicts(db.query_notices(limit=10_000, offset=0, **filters))
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        headers = ["title", "buyer_name", "country", "region", "cpv_codes",
                   "publication_date", "deadline", "value_amount", "value_currency",
                   "source", "score", "url"]
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(h, "") for h in headers])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=veille-{date.today()}.csv"},
        )

    @app.route("/runs")
    def runs_view() -> str:
        with open_db() as db:
            runs = rows_to_dicts(db.recent_runs(limit=50))
            details = {r["id"]: rows_to_dicts(db.source_runs(r["id"])) for r in runs}
        return render_template("runs.html", runs=runs, details=details)

    @app.route("/healthz")
    def healthz() -> Response:
        return Response("ok", mimetype="text/plain")

    return app
