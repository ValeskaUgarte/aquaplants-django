"""Conexión de la app móvil a través de Firebase (sin depender de la misma red WiFi).

El celular ya no busca el computador en la red local. Al arrancar, este servidor publica en Firebase (`config/servidor`) la
dirección por la que se le puede alcanzar desde internet; la app lee esa dirección de Firebase y se conecta. Firebase "da la
conexión": da igual en qué red o con qué datos móviles esté el celular.

Dirección pública, en este orden:
  1. AQ_URL_PUBLICA  (por ejemplo, el servidor desplegado en un hosting, o un túnel con dominio fijo).
  2. Un túnel gratuito de Cloudflare («cloudflared tunnel --url ...»), que se inicia solo si el programa `cloudflared` está
     instalado (AQ_TUNEL=0 lo desactiva). Cada vez que arranca entrega una dirección nueva: se vuelve a publicar sola.
  3. Solo la dirección de la red local (el celular debería estar en la misma WiFi; queda como último recurso).
La dirección es el único dato que se lee sin iniciar sesión (ver firebase/database.rules.json); todo lo demás exige usuario.
"""
import logging
import re
import shutil
import socket
import subprocess
import threading
import time

from django.conf import settings

from core.rtdb import rtdb
from core.util import ahora_ms

log = logging.getLogger("aquaplants.nube")
_estado = {"url": None, "origen": None}


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def publicar(url, origen, puerto):
    """Escribe la dirección en Firebase. Nunca lanza errores."""
    _estado.update(url=url, origen=origen)
    try:
        rtdb().set("config/servidor", {"url": url.rstrip("/"), "origen": origen, "ip_local": f"http://{ip_local()}:{puerto}", "actualizado": ahora_ms()})
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo publicar la dirección del servidor en Firebase: %s", e)


def _latido(puerto):
    while True:
        time.sleep(60)
        if _estado["url"]:
            publicar(_estado["url"], _estado["origen"], puerto)


def _tunel(puerto):
    """Mantiene un túnel de Cloudflare abierto y publica su dirección cada vez que cambia."""
    patron = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    while True:
        try:
            p = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{puerto}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            for linea in p.stderr:
                m = patron.search(linea)
                if m:
                    log.info("Túnel público listo: %s", m.group())
                    publicar(m.group(), "tunel", puerto)
            p.wait()
        except OSError as e:
            log.warning("No se pudo iniciar cloudflared: %s", e)
            return
        time.sleep(5)


def iniciar(puerto):
    """Devuelve un texto para mostrar en la consola de arranque."""
    if rtdb().modo != "firebase":
        return "Sin Firebase: la app móvil solo puede encontrar este servidor por la red local."
    threading.Thread(target=_latido, args=(puerto,), name="latido-nube", daemon=True).start()
    fija = (settings.AQ.get("URL_PUBLICA") or "").strip()
    if fija:
        publicar(fija, "fija", puerto)
        return f"Publicado en Firebase: {fija}"
    if shutil.which("cloudflared") and settings.AQ.get("TUNEL", True):
        threading.Thread(target=_tunel, args=(puerto,), name="tunel", daemon=True).start()
        return "Abriendo túnel público con cloudflared… la dirección aparece en el registro y se publica sola en Firebase."
    local = f"http://{ip_local()}:{puerto}"
    publicar(local, "red_local", puerto)
    return ("Publicado en Firebase solo la dirección de la red local. Para que el celular se conecte desde cualquier red: "
            "define AQ_URL_PUBLICA en .env o instala cloudflared (ver docs/conexion-movil.md).")
