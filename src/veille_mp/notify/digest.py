"""Generation du digest quotidien (HTML, CSV, Markdown)."""

from __future__ import annotations

import csv
import html
import io
from datetime import date
from pathlib import Path
from typing import Any, Iterable

COLUMNS = [
    ("title", "Titre"),
    ("buyer_name", "Pouvoir adjudicateur"),
    ("country", "Pays"),
    ("region", "Region"),
    ("cpv_codes", "CPV"),
    ("publication_date", "Publication"),
    ("deadline", "Date limite"),
    ("value_amount", "Budget estime"),
    ("value_currency", "Devise"),
    ("source", "Source"),
    ("score", "Score"),
    ("url", "Lien"),
]

CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
     margin:0;padding:24px;background:#f6f7f9;color:#1b1f24;line-height:1.45}
h1{font-size:20px;margin:0 0 4px}
.meta{color:#5b6572;font-size:13px;margin-bottom:20px}
table{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;
      box-shadow:0 1px 3px rgba(0,0,0,.08);font-size:13px}
th{background:#eef1f5;text-align:left;padding:10px;font-weight:600;white-space:nowrap}
td{padding:10px;border-top:1px solid #e6e9ee;vertical-align:top}
tr:hover td{background:#fafbfc}
.t{font-weight:600;max-width:420px}
a{color:#1a56db;text-decoration:none}
a:hover{text-decoration:underline}
.tag{display:inline-block;background:#eef1f5;border-radius:4px;padding:1px 6px;margin:1px 2px 1px 0;
     font-size:11px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.soon{color:#b42318;font-weight:600}
.empty{background:#fff;padding:24px;border-radius:8px;text-align:center;color:#5b6572}
.err{background:#fef3f2;border:1px solid #fda29b;color:#912018;padding:10px 12px;
     border-radius:6px;margin-bottom:16px;font-size:13px}
"""


def _fmt_value(row: dict, key: str) -> str:
    value = row.get(key)
    if value in (None, ""):
        return ""
    if key == "value_amount":
        return f"{float(value):,.0f}".replace(",", " ")
    if key == "score":
        return f"{float(value):.2f}"
    return str(value)


def build_digest(rows: Iterable[dict], *, run_date: date | None = None,
                 errors: list[str] | None = None, title: str = "Veille marches publics") -> dict[str, str]:
    """Retourne {"html": ..., "csv": ..., "md": ...}."""
    rows = list(rows)
    run_date = run_date or date.today()
    errors = errors or []

    # --- HTML ---
    parts = [
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{html.escape(title)} - {run_date.isoformat()}</title>",
        f"<style>{CSS}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
        f"<div class='meta'>{run_date.isoformat()} &middot; {len(rows)} avis</div>",
    ]
    for err in errors:
        parts.append(f"<div class='err'>Source en echec : {html.escape(err)}</div>")

    if not rows:
        parts.append("<div class='empty'>Aucun nouveau marche detecte pour cette periode.</div>")
    else:
        parts.append("<table><thead><tr>")
        for _, label in COLUMNS:
            if label == "Lien":
                continue
            parts.append(f"<th>{html.escape(label)}</th>")
        parts.append("</tr></thead><tbody>")
        for row in rows:
            parts.append("<tr>")
            for key, label in COLUMNS:
                if label == "Lien":
                    continue
                raw = _fmt_value(row, key)
                if key == "title":
                    url = row.get("url") or ""
                    inner = html.escape(raw)
                    cell = f"<a href='{html.escape(url)}' target='_blank' rel='noopener'>{inner}</a>" if url else inner
                    parts.append(f"<td class='t'>{cell}</td>")
                elif key == "cpv_codes":
                    tags = "".join(
                        f"<span class='tag'>{html.escape(c)}</span>"
                        for c in filter(None, raw.split(","))
                    )
                    parts.append(f"<td>{tags}</td>")
                elif key == "deadline":
                    cls = " class='soon'" if raw and raw <= (run_date.isoformat()) else ""
                    parts.append(f"<td{cls}>{html.escape(raw)}</td>")
                else:
                    parts.append(f"<td>{html.escape(raw)}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    html_out = "".join(parts)

    # --- CSV ---
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([label for _, label in COLUMNS])
    for row in rows:
        writer.writerow([_fmt_value(row, key) for key, _ in COLUMNS])
    csv_out = buffer.getvalue()

    # --- Markdown ---
    md = [f"# {title} - {run_date.isoformat()}", "", f"**{len(rows)} avis**", ""]
    for err in errors:
        md.append(f"> Source en echec : {err}")
    if errors:
        md.append("")
    if not rows:
        md.append("_Aucun nouveau marche detecte._")
    for row in rows:
        title_text = row.get("title") or "(sans titre)"
        url = row.get("url") or ""
        md.append(f"### {title_text}" if not url else f"### [{title_text}]({url})")
        bits = [
            f"**Acheteur** : {row.get('buyer_name') or 'n/c'}",
            f"**Pays/region** : {row.get('country') or '?'} {row.get('region') or ''}".strip(),
            f"**CPV** : {row.get('cpv_codes') or 'n/c'}",
            f"**Publication** : {row.get('publication_date') or 'n/c'}",
            f"**Date limite** : {row.get('deadline') or 'n/c'}",
        ]
        if row.get("value_amount"):
            bits.append(f"**Budget estime** : {_fmt_value(row, 'value_amount')} {row.get('value_currency') or ''}".strip())
        bits.append(f"**Source** : {row.get('source')} (score {_fmt_value(row, 'score')})")
        md.append("  \n".join(bits))
        md.append("")
    md_out = "\n".join(md)

    return {"html": html_out, "csv": csv_out, "md": md_out}


def write_digests(digest: dict[str, str], output_dir: Path, formats: list[str],
                  run_date: date | None = None) -> dict[str, Path]:
    run_date = run_date or date.today()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for fmt in formats:
        if fmt not in digest:
            continue
        path = output_dir / f"digest-{run_date.isoformat()}.{fmt}"
        path.write_text(digest[fmt], encoding="utf-8")
        written[fmt] = path
    return written
