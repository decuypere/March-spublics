"""Deduplication inter-sources.

Un meme marche peut apparaitre sur TED et sur e-Notification. On regroupe
les avis par empreinte (acheteur + titre + echeance) et, a defaut, par
similarite de titre au sein d'un meme acheteur.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .db import Database
from .models import Notice
from .textutil import normalize


class Deduplicator:
    def __init__(self, cfg: dict, db: Database | None = None):
        self.priority = list(cfg.get("source_priority") or [])
        self.title_similarity = float(cfg.get("title_similarity", 0.92))
        self.db = db

    def _rank(self, notice: Notice) -> int:
        try:
            return self.priority.index(notice.source)
        except ValueError:
            return len(self.priority)

    @staticmethod
    def _fingerprint_is_reliable(notice: Notice) -> bool:
        """Une empreinte batie sur un titre generique sans acheteur ni echeance
        rapprocherait des marches sans rapport: on ne l'utilise pas."""
        if len(normalize(notice.title)) < 15:
            return False
        return bool(normalize(notice.buyer_name)) or notice.deadline is not None

    def _similar(self, a: Notice, b: Notice) -> bool:
        buyer = normalize(a.buyer_name)
        if not buyer or buyer != normalize(b.buyer_name):
            return False
        ta, tb = normalize(a.title), normalize(b.title)
        if not ta or not tb:
            return False
        return SequenceMatcher(None, ta, tb).ratio() >= self.title_similarity

    def resolve(self, notices: list[Notice]) -> dict[str, str | None]:
        """Retourne {notice_key: canonical_key_ou_None}.

        None = l'avis est canonique. Sinon la valeur pointe vers l'avis
        retenu comme reference (celui de la source la plus prioritaire,
        ou celui deja stocke en base).
        """
        # Tri: source prioritaire d'abord, puis id stable pour un resultat
        # deterministe quel que soit l'ordre de collecte.
        ordered = sorted(notices, key=lambda n: (self._rank(n), n.key))

        canonical_by_fingerprint: dict[str, str] = {}
        canonical_notices: list[Notice] = []
        result: dict[str, str | None] = {}

        for notice in ordered:
            fingerprint = notice.dedup_key()
            # Une empreinte faible (titre generique sans acheteur ni echeance)
            # rapprocherait des marches sans rapport: on ne s'en sert pas.
            reliable = self._fingerprint_is_reliable(notice)
            existing: str | None = None

            if reliable:
                # 1. doublon dans le lot courant
                existing = canonical_by_fingerprint.get(fingerprint)
                # 2. doublon avec l'historique en base
                if existing is None and self.db is not None:
                    stored = self.db.canonical_for_dedup_key(fingerprint)
                    if stored and stored != notice.key:
                        existing = stored
            # 3. rapprochement flou sur le titre: entre sources differentes
            #    seulement, et uniquement si l'acheteur est reellement connu
            if existing is None:
                for other in canonical_notices:
                    if other.source != notice.source and self._similar(other, notice):
                        existing = other.key
                        break

            if existing and existing != notice.key:
                result[notice.key] = existing
            else:
                result[notice.key] = None
                if reliable:
                    canonical_by_fingerprint.setdefault(fingerprint, notice.key)
                canonical_notices.append(notice)

        return result
