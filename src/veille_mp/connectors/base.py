"""Interface commune des connecteurs de sources.

Ajouter une source = sous-classer Connector, definir `type_name`, implementer
`fetch()`, et enregistrer la classe dans connectors/__init__.py.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date, timedelta

from ..http import HttpClient
from ..models import Notice
from ..robots import RobotsGate


class ConnectorError(RuntimeError):
    """Erreur non fatale: journalisee, les autres sources continuent."""


class Connector(ABC):
    #: identifiant utilise dans le champ "type" de la configuration
    type_name: str = "base"

    def __init__(self, spec: dict, http: HttpClient, robots: RobotsGate, global_cfg: dict):
        self.spec = spec
        self.name = spec.get("name", self.type_name)
        self.http = http
        self.robots = robots
        self.cfg = global_cfg
        self.log = logging.getLogger(f"connector.{self.name}")

    # -- helpers ----------------------------------------------------------
    @property
    def lookback_days(self) -> int:
        return int(self.spec.get("lookback_days", 7))

    def date_window(self, today: date | None = None) -> tuple[date, date]:
        end = today or date.today()
        return end - timedelta(days=self.lookback_days), end

    @property
    def target_cpv(self) -> list[str]:
        return list((self.cfg.get("filtering") or {}).get("cpv_codes") or [])

    # -- API --------------------------------------------------------------
    @abstractmethod
    def fetch(self) -> list[Notice]:
        """Recupere les avis bruts et les renvoie normalises."""

    def check(self) -> tuple[bool, str]:
        """Diagnostic de disponibilite (commande `veille doctor`).

        Retourne (ok, message). Par defaut: non implemente.
        """
        return True, "pas de diagnostic specifique"
