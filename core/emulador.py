"""Servidor local compatible con la API REST de Firebase Realtime Database (subconjunto usado por el proyecto).

Permite ejecutar y mostrar el sistema SIN internet ni cuenta de Firebase. Soporta GET/PUT/PATCH/DELETE sobre
cualquier ruta `.json`, PATCH multi-ruta, y las consultas orderBy ("$key" o campo hijo), startAt, endAt,
limitToFirst, limitToLast y shallow. Igual que Firebase, no guarda nulos ni objetos vacíos.
"""
import atexit
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

log = logging.getLogger("aquaplants.emulador")


class Almacen:
    def __init__(self, archivo=""):
        self.datos = {}
        self.lock = threading.RLock()
        self.archivo = Path(archivo) if archivo else None
        self.sucio = False
        if self.archivo and self.archivo.exists():
            try:
                self.datos = json.loads(self.archivo.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("No se pudo leer %s: se empieza vacío.", self.archivo)

    @staticmethod
    def _partes(path):
        return [unquote(p) for p in path.strip("/").split("/") if p]

    def leer(self, path):
        with self.lock:
            nodo = self.datos
            for p in self._partes(path):
                if not isinstance(nodo, dict) or p not in nodo:
                    return None
                nodo = nodo[p]
            return json.loads(json.dumps(nodo)) if nodo != {} else None

    def escribir(self, path, valor):
        with self.lock:
            if isinstance(valor, dict) and set(valor) == {".sv"}:       # {".sv": {"increment": n}} (contadores atómicos)
                inc = valor[".sv"].get("increment", 0) if isinstance(valor[".sv"], dict) else 0
                actual = self.leer(path)
                valor = (actual if isinstance(actual, (int, float)) else 0) + inc
            partes = self._partes(path)
            if not partes:
                self.datos = self._limpiar(valor) or {}
            else:
                nodo = self.datos
                for p in partes[:-1]:
                    if not isinstance(nodo.get(p), dict):
                        nodo[p] = {}
                    nodo = nodo[p]
                v = self._limpiar(valor)
                if v is None:
                    nodo.pop(partes[-1], None)
                else:
                    nodo[partes[-1]] = v
                self._podar(self.datos)
            self.sucio = True

    def actualizar(self, path, mapa):
        with self.lock:
            base = path.strip("/")
            for k, v in mapa.items():
                self.escribir(f"{base}/{k}" if base else k, v)

    @classmethod
    def _limpiar(cls, v):
        if isinstance(v, dict) and v == {".sv": "timestamp"}:          # valor del servidor: hora actual (como Firebase)
            return int(time.time() * 1000)
        if isinstance(v, dict):
            r = {k: cls._limpiar(x) for k, x in v.items()}
            r = {k: x for k, x in r.items() if x is not None}
            return r or None
        return v

    @classmethod
    def _podar(cls, nodo):
        for k in list(nodo):
            if isinstance(nodo[k], dict):
                cls._podar(nodo[k])
                if not nodo[k]:
                    del nodo[k]

    def guardar(self):
        if not self.archivo:
            return
        with self.lock:
            if not self.sucio:
                return
            tmp = self.archivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.datos, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.archivo)
            self.sucio = False


def _consultar(nodo, q):
    """Aplica orderBy/startAt/endAt/limit sobre los hijos de un nodo."""
    if not isinstance(nodo, dict):
        return nodo
    if q.get("shallow") == ["true"]:
        return {k: True for k in nodo}
    orden = json.loads(q["orderBy"][0]) if "orderBy" in q else None
    if orden is None:
        return nodo
    clave = (lambda kv: kv[0]) if orden == "$key" else (lambda kv: (kv[1].get(orden) if isinstance(kv[1], dict) else None))
    items = [kv for kv in nodo.items() if clave(kv) is not None]
    items.sort(key=lambda kv: (clave(kv), kv[0]))
    if "startAt" in q:
        m = json.loads(q["startAt"][0]); items = [kv for kv in items if clave(kv) >= m]
    if "endAt" in q:
        m = json.loads(q["endAt"][0]); items = [kv for kv in items if clave(kv) <= m]
    if "limitToFirst" in q:
        items = items[:int(q["limitToFirst"][0])]
    if "limitToLast" in q:
        n = int(q["limitToLast"][0]); items = items[-n:] if n else []
    return dict(items)


def _manejador(almacen):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _ruta(self):
            u = urlparse(self.path)
            p = u.path
            if p.endswith(".json"):
                p = p[:-5]
            return p, parse_qs(u.query)

        def _responder(self, estado, cuerpo):
            datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
            self.send_response(estado)
            self.send_header("Access-Control-Allow-Origin", "*")          # como la API REST de Firebase
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def _cuerpo(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else None

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            p, q = self._ruta()
            self._responder(200, _consultar(almacen.leer(p), q))

        def do_PUT(self):
            p, _ = self._ruta()
            v = self._cuerpo()
            almacen.escribir(p, v)
            self._responder(200, v)

        def do_PATCH(self):
            p, _ = self._ruta()
            v = self._cuerpo() or {}
            almacen.actualizar(p, v)
            self._responder(200, v)

        def do_DELETE(self):
            p, _ = self._ruta()
            almacen.escribir(p, None)
            self._responder(200, None)
    return H


_servidor = None
_almacen = None


def iniciar_emulador(archivo="", puerto=9199):
    """Arranca el servidor (una vez por proceso). Si el puerto ya está ocupado por otro proceso del proyecto
    (por ejemplo `runserver` ya corriendo), simplemente se conecta a ese."""
    global _servidor, _almacen
    if _servidor is not None:
        return f"http://127.0.0.1:{_servidor.server_address[1]}"
    _almacen = Almacen(archivo)
    try:
        _servidor = ThreadingHTTPServer(("127.0.0.1", puerto), _manejador(_almacen))
    except OSError:
        _almacen = None
        return f"http://127.0.0.1:{puerto}"
    _servidor.daemon_threads = True
    threading.Thread(target=_servidor.serve_forever, name="rtdb-local", daemon=True).start()

    def guardar_periodico():
        while True:
            time.sleep(1)
            _almacen.guardar()
    threading.Thread(target=guardar_periodico, name="rtdb-local-guardar", daemon=True).start()
    atexit.register(lambda: _almacen and _almacen.guardar())
    return f"http://127.0.0.1:{_servidor.server_address[1]}"
