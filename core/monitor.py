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
    cultivos = repo.col("cultivos")         # «dispositivos» viene de la caché: la ingesta la mantiene al día (repo.parchar)
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


def revisar_soluciones() -> int:
    """Una solución informada («quedó solucionado») debe verse en la medición. Si pasó el plazo y el dato sigue igual de mal, la alerta
    vuelve a revisión, queda marcada como «solución no confirmada por la medición», se deja en la auditoría y se avisa; para un corte de
    comunicación se recomienda además derivar a un técnico. Si ya se normalizó, la alerta se resolvió sola y aquí no hay nada que hacer."""
    from core.vistas.operacion import causa_presente
    ahora, mapa, n, usr = ahora_ms(), {}, 0, repo.col("usuarios")
    pendientes = []
    for aid in alertas.abiertas_ids():
        a = rtdb().get(f"alertas/{aid}") or {}
        hasta = a.get("espera_medicion_hasta")
        if a.get("estado") == "revisada" and hasta and hasta <= ahora:
            pendientes.append({**a, "id": aid})
    for a in pendientes:
        mapa[f"alertas/{a['id']}/espera_medicion_hasta"] = None
        presente, detalle = causa_presente(a)
        if not presente:
            continue
        quien = usr.get(a.get("revision_por"), {}).get("nombre", "Quien revisó")
        motivo = f"{quien} informó que quedó solucionado, pero {detalle}."
        tecnico = a.get("tipo") == "sin_comunicacion" or bool(a.get("cuestionadas"))
        mapa[f"alertas/{a['id']}/estado"] = "reconocida"                 # vuelve a revisión
        alertas.cuestionar(a["id"], a.get("revision_por"), motivo, mapa, requiere_tecnico=tecnico)
        notificaciones.por_cuestionada(a["id"], a, motivo, tecnico, mapa)
        n += 1
    if mapa:
        try:
            rtdb().update(mapa)
        except Exception:
            notificaciones.descartar_push()
            raise
        from core import seguridad
        for a in pendientes:
            if mapa.get(f"alertas/{a['id']}/solucion_cuestionada"):
                seguridad.registrar_auditoria(accion="solucion_cuestionada", entidad="alerta", entidad_id=a["id"],
                                              detalle={"persona": a.get("revision_por"), "tipo": a.get("tipo")})
        notificaciones.despachar_push()
        cache_micro.limpiar()
    return n


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
        revisar_soluciones()
        depurar()
    except Exception:  # noqa: BLE001
        log.exception("fallo en tarea periódica")
    try:
        from core import respaldo
        respaldo.automatico()
    except Exception:  # noqa: BLE001
        log.exception("fallo al programar el respaldo")
