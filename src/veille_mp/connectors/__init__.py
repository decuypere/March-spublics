"""Registre des connecteurs."""

from __future__ import annotations

from .base import Connector, ConnectorError
from .ted import TedConnector
from .rss import RssConnector
from .http_search import HttpSearchConnector
from .sample import SampleConnector

REGISTRY: dict[str, type[Connector]] = {
    cls.type_name: cls
    for cls in (TedConnector, RssConnector, HttpSearchConnector, SampleConnector)
}


def build_connector(spec: dict, http, robots, global_cfg) -> Connector:
    type_name = spec.get("type")
    if type_name not in REGISTRY:
        raise ConnectorError(
            f"Type de source inconnu: {type_name!r}. Types disponibles: "
            + ", ".join(sorted(REGISTRY))
        )
    return REGISTRY[type_name](spec, http, robots, global_cfg)


__all__ = ["Connector", "ConnectorError", "REGISTRY", "build_connector"]
