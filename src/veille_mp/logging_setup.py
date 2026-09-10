"""Configuration du logging (console + fichier rotatif)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(level: str = "INFO", file_path: str | Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(console)

    if file_path:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5,
                                           encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(file_handler)

    for noisy in ("urllib3", "werkzeug", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
