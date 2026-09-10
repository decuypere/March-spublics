"""Chargement et acces a la configuration YAML."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "VEILLE_"
ENV_SEP = "__"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "config.example.yaml"


class ConfigError(RuntimeError):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce_env_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if "," in raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return raw


def _apply_env_overrides(data: dict) -> dict:
    """VEILLE_NOTIFY__EMAIL__PASSWORD=x  ->  data['notify']['email']['password']=x"""
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        path = env_key[len(ENV_PREFIX):].lower().split(ENV_SEP)
        if not path or not path[0]:
            continue
        cursor = data
        for part in path[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path[-1]] = _coerce_env_value(env_value)
    return data


class Config:
    """Wrapper leger avec acces par chemin pointe."""

    def __init__(self, data: dict, path: Path | None = None):
        self.data = data
        self.path = path

    # -- acces ------------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        cursor: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def __getitem__(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise ConfigError(f"Cle de configuration manquante: {dotted}")
        return value

    # -- helpers ----------------------------------------------------------
    @property
    def sources(self) -> list[dict]:
        return list(self.get("sources", []) or [])

    def enabled_sources(self, only: list[str] | None = None) -> list[dict]:
        out = []
        for src in self.sources:
            name = src.get("name")
            if only:
                if name not in only:
                    continue
            elif not src.get("enabled", False):
                continue
            out.append(src)
        return out

    def resolve_path(self, dotted: str, default: str) -> Path:
        raw = self.get(dotted, default) or default
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def db_path(self) -> Path:
        return self.resolve_path("database.path", "data/veille.sqlite3")


def load_config(path: str | Path | None = None) -> Config:
    """Charge config.yaml (defaults = config.example.yaml) + surcharges env."""
    base: dict = {}
    if EXAMPLE_CONFIG_PATH.exists():
        base = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")) or {}

    chosen = Path(path) if path else DEFAULT_CONFIG_PATH
    if path and not chosen.exists():
        raise ConfigError(f"Fichier de configuration introuvable: {chosen}")

    if chosen.exists():
        user_data = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}
        # Les sources sont remplacees (pas fusionnees) si l'utilisateur en definit.
        if "sources" in user_data:
            base.pop("sources", None)
        merged = _deep_merge(base, user_data)
    else:
        merged = base

    merged = _apply_env_overrides(merged)
    return Config(merged, chosen if chosen.exists() else EXAMPLE_CONFIG_PATH)
