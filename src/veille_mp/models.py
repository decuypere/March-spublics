"""Modele normalise d'un avis de marche, commun a toutes les sources."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any

from .textutil import normalize, sha1


@dataclass
class Notice:
    """Un avis de marche, normalise."""

    source: str                       # nom du connecteur (ted, enot_rss, ...)
    source_id: str                    # identifiant stable chez la source
    title: str = ""
    url: str = ""
    buyer_name: str = ""
    country: str = ""                 # ISO 2 lettres quand disponible
    region: str = ""                  # NUTS ou libelle
    cpv_codes: list[str] = field(default_factory=list)
    publication_date: date | None = None
    deadline: date | None = None
    value_amount: float | None = None
    value_currency: str = ""
    notice_type: str = ""
    description: str = ""
    language: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    # Rempli par le pipeline
    score: float = 0.0
    matched_cpv: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)

    # -- cles -------------------------------------------------------------
    @property
    def key(self) -> str:
        """Cle d'identite intra-source."""
        return f"{self.source}:{self.source_id}"

    def dedup_key(self) -> str:
        """Empreinte inter-sources: acheteur + titre + echeance.

        Deux avis publies sur des canaux differents pour le meme marche
        produisent la meme empreinte.
        """
        return sha1(
            normalize(self.buyer_name),
            normalize(self.title),
            self.deadline.isoformat() if self.deadline else "",
        )

    def searchable_text(self) -> str:
        return " ".join([self.title or "", self.description or "", self.buyer_name or ""])

    # -- serialisation ----------------------------------------------------
    def to_row(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "notice_key": self.key,
            "dedup_key": self.dedup_key(),
            "title": self.title,
            "url": self.url,
            "buyer_name": self.buyer_name,
            "country": self.country,
            "region": self.region,
            "cpv_codes": ",".join(self.cpv_codes),
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "value_amount": self.value_amount,
            "value_currency": self.value_currency,
            "notice_type": self.notice_type,
            "description": self.description,
            "language": self.language,
            "score": round(self.score, 4),
            "matched_cpv": ",".join(self.matched_cpv),
            "matched_keywords": ",".join(self.matched_keywords),
            "raw_json": json.dumps(self.raw, ensure_ascii=False, default=str)[:200_000],
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        data["publication_date"] = self.publication_date.isoformat() if self.publication_date else None
        data["deadline"] = self.deadline.isoformat() if self.deadline else None
        return data


@dataclass
class SourceResult:
    """Resultat d'execution d'un connecteur (une source ne doit jamais
    bloquer les autres: les erreurs sont capturees ici)."""

    name: str
    ok: bool = True
    fetched: int = 0
    error: str = ""
    duration_seconds: float = 0.0
    notices: list[Notice] = field(default_factory=list)
