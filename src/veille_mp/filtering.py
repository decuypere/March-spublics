"""Filtrage par codes CPV + mots-cles, avec score de pertinence configurable."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Notice
from .textutil import normalize

CPV_CLEAN_RE = re.compile(r"[^0-9]")


def clean_cpv(code: str) -> str:
    """'71200000-0' -> '71200000'."""
    return CPV_CLEAN_RE.sub("", str(code or ""))[:8]


@dataclass
class Verdict:
    keep: bool
    score: float
    matched_cpv: list[str]
    matched_keywords: list[str]
    reason: str = ""


class RelevanceFilter:
    """Calcule un score 0..1 a partir des CPV et des mots-cles.

    Regles:
      - CPV exactement dans la liste          -> score plein (garde forcee optionnelle)
      - CPV dans une famille surveillee       -> poids "cpv_prefix"
      - mot-cle dans le titre / la description -> poids correspondant
      - mot-cle d'exclusion dans le titre     -> rejet immediat
    """

    def __init__(self, cfg: dict):
        self.min_score = float(cfg.get("min_score", 0.5))
        weights = cfg.get("weights") or {}
        self.w_cpv_exact = float(weights.get("cpv_exact", 1.0))
        self.w_cpv_prefix = float(weights.get("cpv_prefix", 0.6))
        self.w_kw_title = float(weights.get("keyword_title", 0.45))
        self.w_kw_body = float(weights.get("keyword_body", 0.2))
        self.w_kw_extra = float(weights.get("keyword_extra", 0.1))
        self.cpv_exact_always_keep = bool(cfg.get("cpv_exact_always_keep", True))

        self.cpv_codes = {clean_cpv(c) for c in (cfg.get("cpv_codes") or []) if clean_cpv(c)}
        self.cpv_prefixes = tuple(
            str(p).strip() for p in (cfg.get("cpv_prefixes") or []) if str(p).strip()
        )

        keywords_cfg = cfg.get("keywords") or {}
        raw_keywords: list[str] = []
        if isinstance(keywords_cfg, dict):
            for lang_keywords in keywords_cfg.values():
                raw_keywords.extend(lang_keywords or [])
        else:
            raw_keywords.extend(keywords_cfg or [])
        # dedup en conservant l'ordre, sur forme normalisee
        seen: set[str] = set()
        self.keywords: list[str] = []
        for kw in raw_keywords:
            norm = normalize(kw)
            if norm and norm not in seen:
                seen.add(norm)
                self.keywords.append(norm)

        self.exclude_keywords = [
            normalize(k) for k in (cfg.get("exclude_keywords") or []) if normalize(k)
        ]

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _contains(haystack: str, needle: str) -> bool:
        """Correspondance sur limites de mots (evite 'art' dans 'depart')."""
        if not needle or not haystack:
            return False
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None

    def cpv_match(self, codes: list[str]) -> tuple[list[str], list[str]]:
        exact, prefix = [], []
        for raw in codes:
            code = clean_cpv(raw)
            if not code:
                continue
            if code in self.cpv_codes:
                exact.append(code)
            elif self.cpv_prefixes and code.startswith(self.cpv_prefixes):
                prefix.append(code)
        return exact, prefix

    # -- API --------------------------------------------------------------
    def evaluate(self, notice: Notice) -> Verdict:
        title_norm = normalize(notice.title)
        body_norm = normalize(f"{notice.description} {notice.buyer_name}")

        for bad in self.exclude_keywords:
            if self._contains(title_norm, bad) or self._contains(body_norm, bad):
                return Verdict(False, 0.0, [], [], reason=f"exclu par mot-cle '{bad}'")

        exact, prefix = self.cpv_match(notice.cpv_codes)

        kw_title = [k for k in self.keywords if self._contains(title_norm, k)]
        kw_body = [k for k in self.keywords if k not in kw_title and self._contains(body_norm, k)]

        score = 0.0
        if exact:
            score += self.w_cpv_exact
        elif prefix:
            score += self.w_cpv_prefix

        if kw_title:
            score += self.w_kw_title + self.w_kw_extra * (len(kw_title) - 1)
        if kw_body:
            score += self.w_kw_body + self.w_kw_extra * max(0, len(kw_body) - 1) * 0.5

        score = max(0.0, min(1.0, score))
        matched_kw = kw_title + kw_body

        if exact and self.cpv_exact_always_keep:
            return Verdict(True, max(score, self.w_cpv_exact), exact + prefix, matched_kw,
                           reason="CPV exact")
        keep = score >= self.min_score
        reason = "score >= seuil" if keep else f"score {score:.2f} < seuil {self.min_score:.2f}"
        return Verdict(keep, score, exact + prefix, matched_kw, reason=reason)

    def apply(self, notices: list[Notice]) -> tuple[list[Notice], list[tuple[Notice, Verdict]]]:
        """Retourne (gardes, rejetes_avec_verdict)."""
        kept: list[Notice] = []
        rejected: list[tuple[Notice, Verdict]] = []
        for notice in notices:
            verdict = self.evaluate(notice)
            if verdict.keep:
                notice.score = verdict.score
                notice.matched_cpv = verdict.matched_cpv
                notice.matched_keywords = verdict.matched_keywords
                kept.append(notice)
            else:
                rejected.append((notice, verdict))
        return kept, rejected
