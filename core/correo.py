"""Envío de correos (recuperación de contraseña). Con AQ_SMTP_HOST configurado se envía por SMTP; si no, el mensaje queda en el registro
del servidor y en data/correos-salientes.log para poder probar el flujo sin servidor de correo."""
import logging
import smtplib
import threading
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings

log = logging.getLogger("aquaplants.correo")


def _smtp(msg):
    c = settings.AQ
    try:
        with smtplib.SMTP(c["SMTP_HOST"], c["SMTP_PORT"], timeout=20) as s:
            if c["SMTP_TLS"]:
                s.starttls()
            if c["SMTP_USER"]:
                s.login(c["SMTP_USER"], c["SMTP_PASSWORD"])
            s.send_message(msg)
    except Exception:  # noqa: BLE001
        log.exception("No se pudo enviar el correo a %s", msg["To"])


def enviar(destino, asunto, texto):
    """Devuelve 'enviado' (SMTP, en segundo plano) o 'registro' (sin SMTP: queda en el log del servidor)."""
    c = settings.AQ
    if c["SMTP_HOST"]:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = asunto, c["SMTP_DE"] or c["SMTP_USER"] or "aquaplants@localhost", destino
        msg.set_content(texto)
        threading.Thread(target=_smtp, args=(msg,), daemon=True).start()
        return "enviado"
    log.warning("CORREO (sin SMTP configurado) para %s — %s\n%s", destino, asunto, texto)
    try:
        with open(Path(settings.DATA_DIR) / "correos-salientes.log", "a", encoding="utf-8") as f:
            f.write(f"--- Para: {destino} · {asunto}\n{texto}\n\n")
    except OSError:
        pass
    return "registro"
