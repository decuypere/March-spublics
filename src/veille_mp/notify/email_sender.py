"""Envoi du digest par email (SMTP)."""

from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)


def send_digest_email(cfg: dict, digest: dict[str, str], count: int,
                      run_date: date | None = None,
                      attachments: dict[str, Path] | None = None) -> bool:
    """Retourne True si un email a ete envoye."""
    if not cfg.get("enabled"):
        log.info("Email desactive (notify.email.enabled = false)")
        return False

    recipients = [r for r in (cfg.get("recipients") or []) if r]
    if not recipients:
        log.warning("Email active mais aucun destinataire configure")
        return False

    if count == 0 and cfg.get("skip_if_empty", True):
        log.info("Aucun nouveau marche: email non envoye (skip_if_empty)")
        return False

    run_date = run_date or date.today()
    subject_tpl = cfg.get("subject") or "[Veille] {count} nouveau(x) marche(s) - {date}"
    message = EmailMessage()
    message["Subject"] = subject_tpl.format(count=count, date=run_date.isoformat())
    message["From"] = cfg.get("sender") or (cfg.get("username") or "veille@localhost")
    message["To"] = ", ".join(recipients)
    message.set_content(digest.get("md", "") or "Digest vide.")
    if digest.get("html"):
        message.add_alternative(digest["html"], subtype="html")

    for fmt, path in (attachments or {}).items():
        if fmt == "csv" and path.exists():
            message.add_attachment(
                path.read_bytes(), maintype="text", subtype="csv", filename=path.name
            )

    host = cfg.get("smtp_host")
    port = int(cfg.get("smtp_port", 587))
    if not host:
        raise RuntimeError("notify.email.smtp_host est requis quand l'email est active")

    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
    try:
        if port != 465 and cfg.get("use_tls", True):
            server.starttls()
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password") or "")
        server.send_message(message)
    finally:
        server.quit()

    log.info("Digest envoye a %s", ", ".join(recipients))
    return True
