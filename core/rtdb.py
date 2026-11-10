"""Cliente de Firebase Realtime Database (API REST) — la ÚNICA base de datos del proyecto.

Dos modos, mismo código:
  * Firebase real : AQ_FIREBASE_URL + archivo de credenciales de cuenta de servicio (gratis en el plan Spark).
  * Local         : sin configuración, arranca un servidor compatible con la API REST de Firebase (core/emulador.py)
                    y guarda en data/rtdb-local.json. Sirve para trabajar/mostrar sin internet.
"""
import logging
import threading
import time
from pathlib import Path

import requests
from django.conf import settings

log = logging.getLogger("aquaplants.rtdb")
SCOPES = ["https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"]


class ErrorRTDB(Exception):
    def __init__(self, mensaje, estado=None):
        super().__init__(mensaje)
        self.estado = estado


def _a_dict(x):
    """Firebase convierte claves enteras consecutivas en listas: lo normalizamos a dict."""
    if isinstance(x, list):
        return {str(i): v for i, v in enumerate(x) if v is not None}
    return x


class ClienteRTDB:
    def __init__(self, url, credenciales=None, modo="local"):
        self.url = url.rstrip("/")
        self.modo = modo
        self._credenciales = None
        self._local = threading.local()
        if credenciales:
            from google.oauth2 import service_account
            self._credenciales = service_account.Credentials.from_service_account_file(credenciales, scopes=SCOPES)

    def _sesion(self):
        s = getattr(self._local, "s", None)
        if s is None:
            if self._credenciales is not None:
                from google.auth.transport.requests import AuthorizedSession
                s = AuthorizedSession(self._credenciales)
            else:
                s = requests.Session()
            self._local.s = s
        return s

    def _pedir(self, metodo, path, params=None, cuerpo=None):
        url = f"{self.url}/{path.strip('/')}.json" if path.strip("/") else f"{self.url}/.json"
        for intento in (1, 2):
            try:
                r = self._sesion().request(metodo, url, params=params, json=cuerpo, timeout=15)
                break
            except (requests.ConnectionError, requests.Timeout) as e:
                if intento == 2:
                    raise ErrorRTDB(f"No se pudo conectar con la base de datos ({e.__class__.__name__}).") from e
                time.sleep(0.3)
            except Exception as e:  # noqa: BLE001  (errores de autenticación de google-auth)
                nombre = e.__class__.__name__
                if "Refresh" in nombre or "Auth" in nombre or "Transport" in nombre:
                    raise ErrorRTDB(f"Firebase rechazó las credenciales o no hubo conexión para autenticarse ({nombre}). "
                                    "Revisa AQ_FIREBASE_URL y el archivo de credenciales.") from e
                raise
        if r.status_code >= 400:
            raise ErrorRTDB(f"Firebase respondió {r.status_code}: {r.text[:200]}", r.status_code)
        return r.json() if r.content else None

    # ---------------------------------------------------------------- lectura
    def get(self, path, defecto=None):
        v = _a_dict(self._pedir("GET", path))
        return defecto if v is None else v

    def rango(self, path, desde=None, hasta=None, ultimos=None, primeros=None):
        """Hijos ordenados por clave. Devuelve lista [(clave, valor)] ordenada ascendente."""
        import json as _j
        params = {"orderBy": '"$key"'}
        if desde is not None:
            params["startAt"] = _j.dumps(desde)
        if hasta is not None:
            params["endAt"] = _j.dumps(hasta)
        if ultimos:
            params["limitToLast"] = int(ultimos)
        if primeros:
            params["limitToFirst"] = int(primeros)
        d = _a_dict(self._pedir("GET", path, params)) or {}
        return sorted(d.items())

    def claves(self, path):
        d = self._pedir("GET", path, {"shallow": "true"}) or {}
        return sorted(d) if isinstance(d, dict) else []

    # ---------------------------------------------------------------- escritura
    def set(self, path, valor):
        self._pedir("PUT", path, cuerpo=valor)

    def update(self, mapa):
        """Escritura atómica multi-ruta: {"a/b": 1, "c/d": None (borra)}."""
        if mapa:
            self._pedir("PATCH", "", cuerpo=mapa)

    def borrar(self, path):
        self._pedir("DELETE", path)


_cliente = None
_candado = threading.Lock()


def rtdb() -> ClienteRTDB:
    global _cliente
    if _cliente is not None:
        return _cliente
    with _candado:
        if _cliente is None:
            url = settings.AQ["FIREBASE_URL"]
            if url:
                cred = settings.AQ["FIREBASE_CREDENCIALES"]
                if not Path(cred).exists():
                    raise ErrorRTDB(f"Falta el archivo de credenciales de Firebase: {cred}")
                _cliente = ClienteRTDB(url, cred, "firebase")
                log.info("Base de datos: Firebase Realtime Database (%s)", url)
            else:
                from core.emulador import iniciar_emulador
                local = iniciar_emulador(settings.AQ["RTDB_LOCAL_ARCHIVO"], settings.AQ["RTDB_LOCAL_PUERTO"])
                _cliente = ClienteRTDB(local, None, "local")
                log.info("Base de datos: modo LOCAL compatible con Firebase (%s). Configura AQ_FIREBASE_URL para usar la nube.", local)
    return _cliente


def reiniciar_cliente():
    global _cliente
    _cliente = None
