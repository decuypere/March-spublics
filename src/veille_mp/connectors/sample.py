"""Jeu de donnees hors ligne, pour valider le filtrage et la deduplication
sans dependre d'une source externe (`veille test --source sample`)."""

from __future__ import annotations

import json
from importlib import resources

from .base import Connector
from ..models import Notice
from ..textutil import parse_amount, parse_date


class SampleConnector(Connector):
    type_name = "sample"

    def _load(self) -> list[dict]:
        path = self.spec.get("path")
        if path:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        data = resources.files("veille_mp.fixtures").joinpath("sample_notices.json")
        return json.loads(data.read_text(encoding="utf-8"))

    def fetch(self) -> list[Notice]:
        return [
            Notice(
                source=self.name,
                source_id=str(item["id"]),
                title=item.get("title", ""),
                url=item.get("url", ""),
                buyer_name=item.get("buyer", ""),
                country=item.get("country", ""),
                region=item.get("region", ""),
                cpv_codes=[str(c) for c in item.get("cpv", [])],
                publication_date=parse_date(item.get("publication_date")),
                deadline=parse_date(item.get("deadline")),
                value_amount=parse_amount(item.get("value_amount")),
                value_currency=item.get("value_currency", ""),
                notice_type=item.get("notice_type", ""),
                description=item.get("description", ""),
                raw=item,
            )
            for item in self._load()
        ]

    def check(self) -> tuple[bool, str]:
        return True, f"{len(self._load())} avis d'exemple embarques"
