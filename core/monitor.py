"""Tareas periódicas: detectar dispositivos sin comunicación y depurar sesiones y, si se configura, históricos antiguos."""
import logging
import time

from core import alertas, cache_micro, notificaciones, repo
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms, ms_a_iso

log = logging.getLogger("aquaplants.monitor")
_ultima_depuracion = 0.0


def revisar_dispositivos() -> int:
    """Marca como sin comunicación a los dispositivos en línea que no reportan dentro del plazo configurado."""
    minutos = max(1, repo.parametro_int("minutos_sin_comunicacion", 5))
    limite, ahora, mapa, caidos = ahora_ms() - minutos * 60_000, ahora_ms(), {}, 0
    repo.invalidar("dispositivos")
    cultivos = repo.col("cultivos")
    for d in repo.lista("dispositivos"):
        if not d.get("activo", True) or d.get("estado_conexion") != "en_linea" or (d.get("ultima_comunicacion") or 0) >= limite:
            continue
        mapa[f"dispositivos/{d['id']}/estado_conexion"] = "sin_comunicacion"
        alertas.abrir(f"d:{d['id']}", {
            "tipo": "sin_comunicacion", "problema": "Dispositivo sin comunicación", "instalacion_id": d["instalacion_id"], "dispositivo_id": d["id"],
            "cultivo_id": d.get("cultivo_id") or "",
            "mensaje": f"{cultivos.get(d.get('cultivo_id'), {}).get('nombre', 'Sin cultivo')} · el dispositivo {d['codigo']} no se comunica "
                       f"hace más de {minutos} minuto(s)."}, mapa)
        alertas.evento(mapa, d["instalacion_id"], d["id"], "dispositivo_sin_comunicacion",
                       f"Sin comunicación desde {ms_a_iso(d.get('ultima_comunicacion') or 0)}.")
        caidos += 1
    if mapa:
        try:
            rtdb().update(mapa)
        except Exception:
            alertas.reiniciar()
            notificaciones.descartar_push()
            raise
        notificaciones.despachar_push()
        repo.invalidar("dispositivos")
        cache_micro.limpiar()
    return caidos


def depurar():
    """Una vez por hora: sesiones vencidas y, si `retencion_historicos_dias` > 0, mediciones antiguas."""
    global _ultima_depuracion
    if time.time() - _ultima_depuracion < 3600:
        return
    _ultima_depuracion = time.time()
    ahora, c = ahora_ms(), rtdb()
    for h, s in c.get("sesiones", {}).items():
        if s.get("expira", 0) < ahora - 7 * 86_400_000:
            c.update({f"sesiones/{h}": None, f"sesiones_usuario/{s['uid']}/{h}": None})
    notificaciones.depurar()
    dias = repo.parametro_int("retencion_historicos_dias", 0)
    if dias > 0:
        corte = clave_ms(ahora - dias * 86_400_000)
        for sid in c.claves("mediciones"):
            while True:
                viejos = c.rango(f"mediciones/{sid}", hasta=corte, primeros=1000)
                if not viejos:
                    break
                c.update({f"mediciones/{sid}/{k}": None for k, _ in viejos})


def ciclo():
    try:
        revisar_dispositivos()
        depurar()
    except Exception:  # noqa: BLE001
        log.exception("fallo en tarea periódica")
