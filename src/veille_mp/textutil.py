"""Normalisation de texte, dates et montants, tolerante aux formats sources."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Iterable

from dateutil import parser as dateparser

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_TAG_RE = re.compile(r"<[^>]+>")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(value: str | None) -> str:
    """minuscules, sans accents, ponctuation reduite a des espaces."""
    if not value:
        return ""
    text = strip_accents(str(value)).lower()
    text = text.replace("'", " ").replace("’", " ")
    text = _NON_ALNUM_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", str(value))).strip()


def coerce_text(value: Any, preferred_langs: Iterable[str] = ("fra", "fr", "eng", "en", "nld", "nl")) -> str:
    """Aplatit les valeurs multilingues TED/eForms en une chaine lisible.

    Gere: str, nombre, list, dict {"fra": ["..."], "eng": [...]},
    dict {"value": ...}, et imbrications.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [coerce_text(v, preferred_langs) for v in value]
        return " | ".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in preferred_langs:
            if key in value:
                return coerce_text(value[key], preferred_langs)
        for key in ("value", "text", "label", "name", "#text"):
            if key in value:
                return coerce_text(value[key], preferred_langs)
        parts = [coerce_text(v, preferred_langs) for v in value.values()]
        return " | ".join(p for p in parts if p)
    return str(value)


def coerce_list(value: Any) -> list[str]:
    """Aplatit une valeur en liste de chaines non vides."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(coerce_list(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(coerce_list(item))
        return out
    return [str(value)]


def parse_date(value: Any) -> date | None:
    """Parse une date depuis a peu pres n'importe quel format source."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = coerce_text(value).strip()
    if not text:
        return None
    text = text.split(" | ")[0].strip()
    # Formats compacts TED legacy: 20240115 / 20240115+0100
    compact = re.match(r"^(\d{8})", text)
    if compact and not re.search(r"[-/:]", text[:10]):
        try:
            return datetime.strptime(compact.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    try:
        return dateparser.parse(text, dayfirst=False, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = coerce_text(value)
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    # 1.234.567,89 -> 1234567.89 ; 1,234,567.89 -> 1234567.89
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha1(*parts: str) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def dig(data: Any, dotted: str) -> Any:
    """Acces par chemin pointe; 'a.b[]' aplatit la liste finale.

    Exemples: dig(d, "buyer.name"), dig(d, "cpv[]"), dig(d, "items[].id")
    """
    if not dotted:
        return data
    cursor = data
    for part in dotted.split("."):
        explode = part.endswith("[]")
        key = part[:-2] if explode else part
        if key:
            if isinstance(cursor, dict):
                cursor = cursor.get(key)
            elif isinstance(cursor, list):
                cursor = [c.get(key) if isinstance(c, dict) else None for c in cursor]
            else:
                return None
        if explode and cursor is not None:
            cursor = coerce_list(cursor)
        if cursor is None:
            return None
    return cursor
