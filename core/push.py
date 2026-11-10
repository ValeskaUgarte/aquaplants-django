"""Aviso «push» al celular con Firebase Cloud Messaging (FCM, API HTTP v1).

Funciona solo con Firebase real (cuenta de servicio): en modo local no hay nube desde la que enviar, y las notificaciones
se ven igual dentro de la app y la web (campana). Los tokens de cada celular se guardan en `push_tokens/{usuario}/{hash}`
cuando la app Android se vincula (ver core/vistas/vinculacion.py). El mensaje es de solo datos: la app arma la notificación.
"""
import hashlib
import json
import logging
import threading
from pathlib import Path

from django.conf import settings

from core.rtdb import rtdb

log = logging.getLogger("aquaplants.push")
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_sesion = None
_proyecto = None
_candado = threading.Lock()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _sesion_fcm():
    global _sesion, _proyecto
    with _candado:
        if _sesion is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
            ruta = Path(settings.AQ["FIREBASE_CREDENCIALES"])
            _proyecto = json.loads(ruta.read_text(encoding="utf-8"))["project_id"]
            _sesion = AuthorizedSession(service_account.Credentials.from_service_account_file(str(ruta), scopes=[SCOPE]))
        return _sesion, _proyecto


def enviar_en_segundo_plano(uids, titulo, mensaje, alerta_id=""):
    try:
        if rtdb().modo != "firebase":
            return
    except Exception:  # noqa: BLE001
        return
    threading.Thread(target=_enviar, args=(list(uids), titulo, mensaje, alerta_id), name="push", daemon=True).start()


def _enviar(uids, titulo, mensaje, alerta_id):
    try:
        sesion, proyecto = _sesion_fcm()
        url = f"https://fcm.googleapis.com/v1/projects/{proyecto}/messages:send"
        for uid in uids:
            for h, t in (rtdb().get(f"push_tokens/{uid}", {}) or {}).items():
                cuerpo = {"message": {"token": t["token"], "android": {"priority": "HIGH", "ttl": "3600s"},
                                      "data": {"titulo": titulo[:100], "mensaje": mensaje[:300], "alerta_id": alerta_id or ""}}}
                r = sesion.post(url, json=cuerpo, timeout=10)
                if r.status_code in (400, 404) and ("UNREGISTERED" in r.text or "INVALID_ARGUMENT" in r.text):
                    rtdb().borrar(f"push_tokens/{uid}/{h}")          # el celular desinstaló la app o el token venció
                elif r.status_code >= 400:
                    log.warning("FCM respondió %s: %s", r.status_code, r.text[:200])
    except Exception:  # noqa: BLE001
        log.exception("Fallo al enviar notificaciones push")
