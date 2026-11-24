"""Respaldo y restauración de la base (Firebase Realtime Database o modo local) y control del espacio usado.

* `crear()` descarga la base nodo por nodo y la guarda comprimida en `respaldos/respaldo_AAAAMMDD_HHMMSS.json.gz`
  (carpeta configurable con AQ_RESPALDOS_DIR, por ejemplo una carpeta de OneDrive o Google Drive para tener copia fuera del equipo).
* Rotación: se conservan los últimos `respaldos_conservar` respaldos y, además, el más reciente de cada una de las últimas 4 semanas.
* `restaurar()` vuelve a escribir los nodos del archivo (todos o solo los indicados), después de un respaldo de seguridad.
* `revisar_espacio()` compara el tamaño medido con `limite_base_mb` (1 GB en el plan gratuito Spark) y avisa a los
  administradores cuando se supera `aviso_espacio_pct`, como máximo una vez al día.
* `automatico()` lo llama el monitor: hace el respaldo cada `respaldo_cada_horas` horas (0 = desactivado), en un hilo aparte.

Los respaldos contienen datos personales y hashes de contraseñas: la carpeta no se sube a GitHub (.gitignore) y debe guardarse en privado.
"""
import gzip
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings

from core import repo
from core.rtdb import rtdb
from core.util import ahora_ms

log = logging.getLogger("aquaplants.respaldo")
NO_RESPALDAR = {"relay"}                 # peticiones en tránsito de la app móvil: no tiene sentido guardarlas
POR_PARTES = {"mediciones"}              # nodos grandes: se leen y escriben por hijo para no hacer una sola petición enorme
NOMBRE = re.compile(r"^respaldo_(\d{8}_\d{6})(?:_(\d+))?\.json\.gz$")
SEMANAS_CONSERVADAS = 4
_candado = threading.Lock()
_en_curso = False


def carpeta() -> Path:
    c = Path(settings.AQ["RESPALDOS_DIR"])
    c.mkdir(parents=True, exist_ok=True)
    return c


def _leer_nodo(c, nodo):
    if nodo in POR_PARTES:
        return {h: c.get(f"{nodo}/{h}") for h in c.claves(nodo)}
    return c.get(nodo)


def crear(motivo="manual") -> dict:
    """Descarga toda la base y la guarda comprimida. Devuelve {archivo, bytes_base, bytes_archivo, segundos}."""
    global _en_curso
    with _candado:
        if _en_curso:
            raise RuntimeError("Ya hay un respaldo en curso.")
        _en_curso = True
    try:
        t0, c = time.time(), rtdb()
        datos = {}
        for nodo in c.claves(""):
            if nodo not in NO_RESPALDAR:
                datos[nodo] = _leer_nodo(c, nodo)
        crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        fecha = datetime.now()
        destino, n = carpeta() / f"respaldo_{fecha:%Y%m%d_%H%M%S}.json.gz", 1
        while destino.exists():                  # nunca sobrescribir otro respaldo (p. ej. el que se está restaurando)
            destino, n = carpeta() / f"respaldo_{fecha:%Y%m%d_%H%M%S}_{n}.json.gz", n + 1
        meta = {"version": 1, "creado_en": ahora_ms(), "motivo": motivo, "modo": c.modo, "origen": settings.AQ["FIREBASE_URL"] or "local",
                "nodos": sorted(datos), "bytes_base": len(crudo)}
        temporal = destino.with_suffix(".tmp")
        with gzip.open(temporal, "wb") as f:      # se escribe primero a un temporal: un corte a la mitad no deja un respaldo «válido» a medias
            f.write(json.dumps({"meta": meta}, ensure_ascii=False)[:-1].encode("utf-8") + b',"datos":' + crudo + b"}")
        temporal.replace(destino)
        resultado = {"archivo": str(destino), "bytes_base": len(crudo), "bytes_archivo": destino.stat().st_size,
                     "segundos": round(time.time() - t0, 1), "nodos": len(datos)}
        rotar()
        c.update({"estado_base/ultimo_respaldo_en": meta["creado_en"], "estado_base/ultimo_respaldo_archivo": destino.name,
                  "estado_base/ultimo_respaldo_motivo": motivo})
        revisar_espacio(len(crudo))
        log.info("Respaldo creado: %s (%.1f MB de datos, %.1f MB comprimido, %s s)", destino.name, len(crudo) / 1e6,
                 resultado["bytes_archivo"] / 1e6, resultado["segundos"])
        return resultado
    finally:
        with _candado:
            _en_curso = False


def listar() -> list:
    """Respaldos de la carpeta, del más nuevo al más antiguo."""
    salida = []
    for f in carpeta().iterdir():
        m = NOMBRE.match(f.name)
        if m:
            salida.append({"archivo": f.name, "fecha": datetime.strptime(m.group(1), "%Y%m%d_%H%M%S"), "orden": int(m.group(2) or 0),
                           "bytes": f.stat().st_size, "ruta": f})
    return sorted(salida, key=lambda x: (x["fecha"], x["orden"]), reverse=True)


def rotar():
    """Deja los últimos N respaldos y el más reciente de cada una de las últimas semanas; borra el resto."""
    conservar = max(1, repo.parametro_int("respaldos_conservar", 7))
    todos = listar()
    mantener = {r["archivo"] for r in todos[:conservar]}
    limite_semanas = datetime.now() - timedelta(weeks=SEMANAS_CONSERVADAS)
    vistas = set()
    for r in todos:
        semana = r["fecha"].isocalendar()[:2]
        if r["fecha"] >= limite_semanas and semana not in vistas:
            vistas.add(semana)
            mantener.add(r["archivo"])
    for r in todos:
        if r["archivo"] not in mantener:
            try:
                r["ruta"].unlink()
            except OSError:
                log.warning("No se pudo borrar el respaldo antiguo %s", r["archivo"])


def leer(archivo) -> dict:
    ruta = Path(archivo)
    if not ruta.exists():
        ruta = carpeta() / archivo
    with gzip.open(ruta, "rb") if ruta.suffix == ".gz" else open(ruta, "rb") as f:
        contenido = json.loads(f.read().decode("utf-8"))
    if "datos" not in contenido:                       # también acepta una exportación JSON de la consola de Firebase
        contenido = {"meta": {"version": 0}, "datos": contenido}
    return contenido


def restaurar(archivo, nodos=None, respaldo_previo=True) -> dict:
    """Escribe en la base los nodos del respaldo (todos o los indicados). Reemplaza el contenido actual de esos nodos."""
    contenido = leer(archivo)
    datos = contenido["datos"]
    elegidos = [n for n in (nodos or sorted(datos)) if n not in NO_RESPALDAR]
    faltan = [n for n in elegidos if n not in datos]
    if faltan:
        raise ValueError(f"El respaldo no contiene: {', '.join(faltan)}")
    previo = crear("antes de restaurar")["archivo"] if respaldo_previo else None
    c = rtdb()
    for nodo in elegidos:
        valor = datos[nodo]
        if nodo in POR_PARTES and isinstance(valor, dict):
            c.borrar(nodo)
            for hijo, v in valor.items():
                c.set(f"{nodo}/{hijo}", v)
        else:
            c.set(nodo, valor)
    repo.invalidar()
    return {"nodos": elegidos, "respaldo_previo": previo, "creado_en": contenido["meta"].get("creado_en")}


def estado() -> dict:
    e = rtdb().get("estado_base", {}) or {}
    limite_mb = max(1, repo.parametro_int("limite_base_mb", 1024))
    tam = e.get("tamano_bytes")
    return {**e, "limite_mb": limite_mb, "porcentaje": round(100 * tam / (limite_mb * 1_000_000), 1) if tam else None,
            "aviso_pct": repo.parametro_int("aviso_espacio_pct", 80), "cada_horas": repo.parametro_int("respaldo_cada_horas", 24),
            "carpeta": str(carpeta()), "en_curso": _en_curso,
            "respaldos": [{"archivo": r["archivo"], "fecha": r["fecha"].isoformat(timespec="seconds"), "bytes": r["bytes"]} for r in listar()[:12]]}


def revisar_espacio(bytes_base: int):
    """Guarda el tamaño medido y, si pasa el umbral, avisa a los administradores (una vez cada 24 h)."""
    from core import notificaciones
    c = rtdb()
    limite = max(1, repo.parametro_int("limite_base_mb", 1024)) * 1_000_000
    umbral = min(100, max(1, repo.parametro_int("aviso_espacio_pct", 80)))
    pct = 100 * bytes_base / limite
    cambios = {"estado_base/tamano_bytes": bytes_base, "estado_base/medido_en": ahora_ms()}
    previo = c.get("estado_base/aviso_espacio_en") or 0
    avisar = pct >= umbral and ahora_ms() - previo > 86_400_000
    if avisar:
        admins = [u["id"] for u in repo.lista("usuarios") if u.get("rol") == "administrador" and u.get("activo", True) and not u.get("eliminado")]
        notificaciones.avisar(cambios, admins, "espacio_base", "Base de datos casi llena",
                              f"La base usa {bytes_base / 1e6:.0f} MB de {limite / 1e6:.0f} MB ({pct:.0f} %). Respalda y exporta las mediciones "
                              "antiguas, y fija «retencion historicos dias» en Parámetros para liberar espacio.", critica=True)
        cambios["estado_base/aviso_espacio_en"] = ahora_ms()
    c.update(cambios)
    if avisar:
        notificaciones.despachar_push()
        log.warning("La base de datos usa el %.0f %% del límite configurado (%s MB).", pct, limite // 1_000_000)
    return pct


def en_segundo_plano(motivo="manual") -> bool:
    if _en_curso:
        return False

    def tarea():
        try:
            crear(motivo)
        except Exception:  # noqa: BLE001
            log.exception("Falló el respaldo de la base de datos")

    threading.Thread(target=tarea, name="respaldo", daemon=True).start()
    return True


_ultimo_intento = 0.0


def automatico():
    """Lo llama el monitor en cada ciclo: si toca, lanza el respaldo programado (no bloquea el monitor)."""
    global _ultimo_intento
    if os.environ.get("AQ_RESPALDO_AUTOMATICO", "1") == "0":   # p. ej. en Render: su disco se borra en cada reinicio
        return
    horas = repo.parametro_int("respaldo_cada_horas", 24)
    if horas <= 0 or _en_curso or time.time() - _ultimo_intento < 600:      # tras un intento, espera 10 min antes de revisar de nuevo
        return
    recientes = listar()                     # cuenta la carpeta de ESTE servidor: si se cambió de computador, respalda de inmediato
    ultimo = recientes[0]["fecha"].timestamp() if recientes else 0
    if time.time() - ultimo >= horas * 3600:
        _ultimo_intento = time.time()
        en_segundo_plano("automático")
