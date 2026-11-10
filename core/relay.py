"""Relé por Firebase: la app móvil se conecta SOLO a través de Firebase, nunca directo al computador.

Problema que resuelve: el servidor Django corre en un computador (sin dirección pública). Antes el celular tenía que
alcanzarlo por HTTP, y eso solo funcionaba en la misma WiFi. Ahora ambos hablan únicamente con Firebase:

    celular ──PUT relay/sol/{id}──▶ Firebase ──(escucha en tiempo real)──▶ Django ejecuta la petición
    celular ◀──lee relay/resp/{id}── Firebase ◀──────────── escribe la respuesta ──┘

* El celular puede estar con datos móviles o en cualquier WiFi: solo necesita internet (igual que el servidor).
* Cada petición pasa por TODA la seguridad normal de Django (sesión, CSRF, roles, límites, auditoría): el relé arma una
  petición HTTP interna y la entrega al mismo manejador que atiende al navegador. No hay atajos.
* Reglas de Firebase (firebase/database.rules.json): cualquiera puede DEJAR una petición nueva (no leerlas ni pisarlas);
  la respuesta solo la puede leer quien conoce su id (128 bits aleatorios generados en el celular). Django usa la cuenta
  de servicio y no depende de las reglas.
* Las peticiones con más de 60 s se descartan (por ejemplo, las que quedaron mientras el servidor estaba apagado) y
  las respuestas se borran a los 2 minutos.
"""
import base64
import io
import json
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings

from core.rtdb import rtdb
from core.util import ahora_ms

log = logging.getLogger("aquaplants.relay")
PAT_ID = re.compile(r"^\d{13}_[0-9a-f]{32}$")
EDAD_MAXIMA_MS = 90_000
VIDA_RESPUESTA_MS = 120_000
MAX_RESPUESTA = 8_000_000

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="relay")
_vistos, _vistos_lock = {}, threading.Lock()
_respondidas, _resp_lock = [], threading.Lock()     # (escrita_en, id): se borran a los 2 minutos
_estado = {"activo": False, "modo": None, "atendidas": 0, "ultima": None}
_manejador = None


def estado():
    return dict(_estado)


# ------------------------------------------------------------------ ejecución de una petición
def _wsgi():
    global _manejador
    if _manejador is None:
        from django.core.handlers.wsgi import WSGIHandler
        _manejador = WSGIHandler()
    return _manejador


def _host():
    hosts = [h for h in settings.ALLOWED_HOSTS if h and h != "*" and not h.startswith(".")]
    return hosts[0] if hosts else "localhost"


def ejecutar(sol):
    """Ejecuta una petición del relé dentro de Django y devuelve la respuesta en el formato del relé:
    {"s": estado, "b": cuerpo (texto)} o, si no es texto, {"s", "b64", "ct", "fn"}. Si la petición abre o cierra la
    sesión, incluye "k" con el token nuevo (o "" al cerrarla): el celular lo guarda en vez de una cookie."""
    from django.core.handlers.wsgi import WSGIRequest

    metodo = str(sol.get("m") or "GET").upper()
    ruta = str(sol.get("r") or "")
    if metodo not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not ruta.startswith("/api/") or len(ruta) > 600 \
            or ruta.startswith("/api/v1/ingesta"):
        return {"s": 400, "b": json.dumps({"error": {"codigo": "relay_invalido", "mensaje": "Petición no válida."}})}
    partes = urlsplit(ruta)
    cuerpo = str(sol.get("b") or "").encode("utf-8")
    cookie = settings.AQ["COOKIE_SESION"]
    token = str(sol.get("t") or "")
    equipo = re.sub(r"[^0-9a-f]", "", str(sol.get("d") or ""))[:16] or "anonimo"
    host = _host()
    environ = {
        "REQUEST_METHOD": metodo, "PATH_INFO": partes.path, "QUERY_STRING": partes.query, "SCRIPT_NAME": "",
        "SERVER_NAME": host, "SERVER_PORT": "443", "HTTP_HOST": host, "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0), "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(cuerpo), "wsgi.errors": sys.stderr,
        "wsgi.multithread": True, "wsgi.multiprocess": False, "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(cuerpo)), "CONTENT_TYPE": "application/json" if cuerpo else "",
        "REMOTE_ADDR": f"firebase-{equipo}",           # límites de ritmo por celular (no uno compartido por todos)
        "HTTP_ACCEPT": "application/json", "HTTP_USER_AGENT": str(sol.get("ua") or "AquaPlants Android (relé Firebase)")[:200],
        "HTTP_X_AQ_RELAY": "1",
    }
    if token and re.fullmatch(r"[A-Za-z0-9_\-]{10,200}", token):
        environ["HTTP_COOKIE"] = f"{cookie}={token}"
    if sol.get("c"):
        environ["HTTP_X_CSRF_TOKEN"] = str(sol["c"])[:200]
    respuesta = _wsgi().get_response(WSGIRequest(environ))
    contenido = b"".join(respuesta.streaming_content) if getattr(respuesta, "streaming", False) else respuesta.content
    salida = {"s": respuesta.status_code}
    tipo = respuesta.get("Content-Type", "")
    if len(contenido) > MAX_RESPUESTA:
        salida = {"s": 413, "b": json.dumps({"error": {"codigo": "respuesta_grande", "mensaje": "La respuesta es demasiado grande para el celular."}})}
    elif not contenido or tipo.startswith("application/json"):
        salida["b"] = contenido.decode("utf-8")
    else:
        nombre = re.search(r'filename="([^"]+)"', respuesta.get("Content-Disposition", ""))
        salida.update(b64=base64.b64encode(contenido).decode("ascii"), ct=tipo, fn=nombre.group(1) if nombre else "archivo")
    if cookie in respuesta.cookies:
        m = respuesta.cookies[cookie]
        salida["k"] = "" if (m["max-age"] == 0 or not m.value) else m.value
    return salida


def _edad_ms(sid, sol):
    """Antigüedad según la hora de Firebase (`ts` = {".sv": "timestamp"}); no depende del reloj del celular."""
    ts = sol.get("ts") if isinstance(sol, dict) else None
    return ahora_ms() - (ts if isinstance(ts, (int, float)) else int(sid[:13]))


def _atender(sid, sol):
    try:
        if not PAT_ID.match(sid):
            return
        if not isinstance(sol, dict) or _edad_ms(sid, sol) > EDAD_MAXIMA_MS:
            return                                          # vieja (el servidor estaba apagado) o mal formada: se descarta
        try:
            r = ejecutar(sol)
        except Exception:  # noqa: BLE001
            log.exception("relé: fallo al ejecutar %s %s", sol.get("m"), sol.get("r"))
            r = {"s": 500, "b": json.dumps({"error": {"codigo": "error_interno", "mensaje": "Error interno."}})}
        rtdb().set(f"relay/resp/{sid}", {**r, "ts": ahora_ms()})
        with _resp_lock:
            _respondidas.append((time.time(), sid))
        _estado["atendidas"] += 1
        _estado["ultima"] = ahora_ms()
    except Exception:  # noqa: BLE001
        log.exception("relé: no se pudo responder %s", sid)
    finally:
        try:
            rtdb().borrar(f"relay/sol/{sid}")
        except Exception:  # noqa: BLE001
            pass


def _recibir(sid, sol):
    """Encola una petición una sola vez (la escucha y el sondeo de respaldo pueden verla las dos)."""
    if sol is None:
        return
    with _vistos_lock:
        if sid in _vistos:
            return
        ahora = time.time()
        _vistos[sid] = ahora
        if len(_vistos) > 5000:
            for k in [k for k, t in _vistos.items() if t < ahora - 300]:
                del _vistos[k]
    _pool.submit(_atender, sid, sol)


# ------------------------------------------------------------------ escucha
def _escuchar_firebase():
    """Escucha en tiempo real (Server-Sent Events de la API REST de Firebase). Se reconecta sola."""
    c = rtdb()
    url = f"{c.url}/relay/sol.json"
    while True:
        try:
            with c._sesion().get(url, headers={"Accept": "text/event-stream"}, stream=True, timeout=(10, 90)) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"Firebase respondió {r.status_code}")
                _estado.update(activo=True, modo="tiempo_real")
                evento = None
                for linea in r.iter_lines(chunk_size=1, decode_unicode=True):   # sin búfer: cada evento se atiende al llegar
                    if linea is None:
                        continue
                    if linea.startswith("event:"):
                        evento = linea[6:].strip()
                        if evento in ("cancel", "auth_revoked"):
                            break                           # el token caducó (1 h): se reconecta con uno nuevo
                    elif linea.startswith("data:") and evento in ("put", "patch"):
                        d = json.loads(linea[5:].strip() or "null")
                        if not isinstance(d, dict):
                            continue
                        ruta, datos = d.get("path", "/"), d.get("data")
                        if ruta == "/" and isinstance(datos, dict):
                            for sid, sol in datos.items():
                                _recibir(sid, sol)
                        elif ruta.count("/") == 1 and ruta != "/":
                            _recibir(ruta[1:], datos)
                        elif ruta.count("/") > 1:      # un campo suelto: se lee la petición completa
                            sid = ruta.split("/")[1]
                            _recibir(sid, c.get(f"relay/sol/{sid}"))
        except Exception as e:  # noqa: BLE001
            _estado.update(activo=False)
            log.warning("relé: escucha interrumpida (%s); se reconecta en 3 s", e)
            time.sleep(3)


def _sondear():
    """Respaldo (y único modo en la base local): revisa las peticiones pendientes cada 0,5 s y limpia lo viejo."""
    ultima_limpieza = 0.0
    while True:
        try:
            pendientes = rtdb().get("relay/sol") or {}
            if isinstance(pendientes, dict):
                for sid, sol in pendientes.items():
                    if PAT_ID.match(sid) and _edad_ms(sid, sol) > EDAD_MAXIMA_MS:
                        rtdb().borrar(f"relay/sol/{sid}")
                    else:
                        _recibir(sid, sol)
            if _estado["modo"] != "tiempo_real":
                _estado.update(activo=True, modo="sondeo")
            if time.time() - ultima_limpieza > 30:
                ultima_limpieza = time.time()
                with _resp_lock:
                    corte = time.time() - VIDA_RESPUESTA_MS / 1000
                    viejas = [k for t, k in _respondidas if t < corte]
                    _respondidas[:] = [(t, k) for t, k in _respondidas if t >= corte]
                if viejas:
                    rtdb().update({f"relay/resp/{k}": None for k in viejas})
        except Exception as e:  # noqa: BLE001
            log.debug("relé: sondeo fallido (%s)", e)
        time.sleep(0.5 if rtdb().modo != "firebase" or _estado["modo"] != "tiempo_real" else 5)


# ------------------------------------------------------------------ reglas de Firebase
def asegurar_reglas():
    """Publica firebase/database.rules.json en el proyecto si las reglas vigentes no permiten el relé.
    Devuelve un texto para la consola de arranque. Se desactiva con AQ_PUBLICAR_REGLAS=0."""
    c = rtdb()
    if c.modo != "firebase" or not settings.AQ.get("PUBLICAR_REGLAS", True):
        return ""
    archivo = Path(settings.BASE_DIR) / "firebase" / "database.rules.json"
    try:
        r = c._sesion().get(f"{c.url}/.settings/rules.json", timeout=15)
        if r.status_code == 200 and '"relay"' in r.text and '"resp"' in r.text:
            return "Reglas de Firebase: ya permiten la conexión del celular."
        p = c._sesion().put(f"{c.url}/.settings/rules.json", data=archivo.read_bytes(), timeout=15)
        if p.status_code == 200:
            return "Reglas de Firebase actualizadas desde firebase/database.rules.json (conexión del celular habilitada)."
        return f"No se pudieron publicar las reglas de Firebase (HTTP {p.status_code}): pégalas a mano (docs/conexion-movil.md)."
    except Exception as e:  # noqa: BLE001
        return f"No se pudieron revisar las reglas de Firebase ({e.__class__.__name__}): pégalas a mano (docs/conexion-movil.md)."


def iniciar():
    """Arranca la escucha. Devuelve un texto para la consola de arranque."""
    aviso = asegurar_reglas()
    try:
        rtdb().borrar("relay")                              # restos de una ejecución anterior (nadie las espera ya)
    except Exception:  # noqa: BLE001
        pass
    if rtdb().modo == "firebase":
        threading.Thread(target=_escuchar_firebase, name="relay-escucha", daemon=True).start()
    threading.Thread(target=_sondear, name="relay-sondeo", daemon=True).start()
    return aviso
