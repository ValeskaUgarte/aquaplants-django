"""Rutas operativas: estado actual, históricos, alertas, eventos, panel, comparación, carga manual y auditoría."""
import math
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from django.http import JsonResponse

from core import alertas as modulo_alertas
from core import notificaciones
from core import cache_micro
from core import repo, seguridad
from core.http import (Campo, ErrorApi, booleano, conflicto, exigir_acceso_instalacion, fecha_iso, instalaciones_visibles,
                       invalido, ip_cliente, no_encontrado, numero, id_valor, ruta, sin_contenido, texto, validar)
from core.ingesta import procesar_lote
from core.vistas.mantencion import mantencion_publica
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms, iso, iso_a_ms

A_CARGO = ("encargado_cultivo", "cliente", "supervisor", "administrador")          # quienes atienden las alertas
SIN_SUPERIOR = ("supervisor", "administrador", "cliente")                       # nadie por encima que apruebe: su revisión es la definitiva
NOMBRE_ROL = {"administrador": "administrador", "supervisor": "supervisor", "cliente": "cliente"}
BUCKETS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
MAX_PUNTOS = 400
_fresco = cache_micro.fresco
invalidar_micro = cache_micro.limpiar


def _abiertas():
    return [a for a in _fresco("alertas", lambda: modulo_alertas.ultimas(500)) if a["estado"] != "resuelta"]


def _rango(q):
    hasta = ahora_ms()
    try:
        if q.get("hasta"):
            hasta = iso_a_ms(q["hasta"])
        desde = hasta - 24 * 3600_000
        if q.get("desde"):
            desde = iso_a_ms(q["desde"])
    except ValueError:
        raise invalido("Rango de fechas inválido.")
    if desde >= hasta:
        raise invalido('"desde" debe ser anterior a "hasta".')
    if hasta - desde > 366 * 86_400_000:
        raise invalido("El rango máximo es 366 días.")
    return desde, hasta


def _elegir_bucket(desde, hasta, pedido):
    if pedido and pedido != "auto":
        if pedido == "crudo":
            return None
        if pedido not in BUCKETS:
            raise invalido("Agregación no válida.")
        return BUCKETS[pedido]
    seg = (hasta - desde) / 1000
    for b in BUCKETS.values():
        if seg / b <= MAX_PUNTOS:
            return b
    return BUCKETS["1d"]


def _agregar(filas, bucket):
    """filas: [(ms, valor)] -> ventanas de `bucket` segundos con promedio, mínimo, máximo y cantidad."""
    grupos = {}
    for t, v in filas:
        k = math.floor(t / 1000 / bucket) * bucket
        g = grupos.get(k)
        if g is None:
            grupos[k] = [v, v, v, 1]
        else:
            g[0] += v; g[1] = min(g[1], v); g[2] = max(g[2], v); g[3] += 1
    return [{"t": iso(k * 1000), "prom": g[0] / g[3], "min": g[1], "max": g[2], "n": g[3]} for k, g in sorted(grupos.items())]


def _leer_mediciones(sid, desde, hasta, ultimos=20000):
    return [(int(k), v) for k, v in rtdb().rango(f"mediciones/{sid}", desde=clave_ms(desde), hasta=clave_ms(hasta), ultimos=ultimos)]


# ESTADO ACTUAL
@ruta(("GET",))
def estado_actual(request):
    iid = request.GET.get("instalacion_id")
    if not iid:
        raise invalido("instalacion_id es obligatorio.")
    exigir_acceso_instalacion(request.usuario, iid)
    return JsonResponse(_estado_instalacion(iid), safe=False)


def _estado_instalacion(iid):
    disp, tipos, cult = repo.col("dispositivos"), repo.col("tipos_sensor"), repo.col("cultivos")
    ultima = _fresco("ultima", lambda: rtdb().get("ultima", {}))
    abiertas = defaultdict(int)
    for a in _abiertas():
        if a.get("sensor_id"):
            abiertas[a["sensor_id"]] += 1
    filas = []
    for s in repo.lista("sensores"):
        d, t = disp.get(s["dispositivo_id"]), tipos.get(s["tipo_sensor_id"])
        if not d or not t or d["instalacion_id"] != iid or not s.get("activo", True):
            continue
        u = ultima.get(s["id"])
        filas.append({
            "sensor_id": s["id"], "codigo": s["codigo"], "nombre": s["nombre"], "tipo_nombre": t["nombre"],
            "tipo_variable": t["tipo_variable"], "unidad": t["unidad"], "cultivo": cult.get(d.get("cultivo_id"), {}).get("nombre"), "cultivo_id": d.get("cultivo_id"),
            "dispositivo_codigo": d["codigo"], "dispositivo_nombre": d["nombre"], "estado_conexion": d.get("estado_conexion", "sin_datos"),
            "ultima_comunicacion": iso(d.get("ultima_comunicacion")), "calidad_enlace": d.get("calidad_enlace"),
            "valor": u["valor"] if u else None, "medido_en": iso(u["ts"]) if u else None,
            "medicion_conexion": u["conexion"] if u else None, "alertas_abiertas": abiertas.get(s["id"], 0)})
    return sorted(filas, key=lambda f: (f["dispositivo_codigo"], f["codigo"]))


#  HISTÓRICO DE UN SENSOR
@ruta(("GET",))
def mediciones(request):
    sid = request.GET.get("sensor_id")
    if not sid:
        raise invalido("sensor_id es obligatorio.")
    s = repo.get("sensores", sid)
    d = repo.col("dispositivos").get(s["dispositivo_id"]) if s else None
    t = repo.col("tipos_sensor").get(s["tipo_sensor_id"]) if s else None
    if not s or not d or not t:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, d["instalacion_id"])
    desde, hasta = _rango(request.GET)
    bucket = _elegir_bucket(desde, hasta, str(request.GET.get("agregacion", "auto")))
    filas = _leer_mediciones(sid, desde, hasta)
    if bucket is None:
        puntos = [{"t": iso(t_), "valor": v["v"], "estado_conexion": v.get("e", "conectado")} for t_, v in filas[-5000:]]
    else:
        puntos = _agregar([(t_, v["v"]) for t_, v in filas], bucket)
    return JsonResponse({"sensor": {"id": sid, "codigo": s["codigo"], "tipo": t["nombre"], "unidad": t["unidad"]},
                         "desde": iso(desde), "hasta": iso(hasta), "bucket_segundos": bucket, "puntos": puntos})


# CARGA MANUAL (solo administrador)
@ruta(("POST",), roles=("administrador",))
def medicion_manual(request):
    d = validar(request.cuerpo, {"sensor_id": Campo(id_valor), "valor": Campo(numero), "timestamp": Campo(fecha_iso, requerido=False)})
    s = repo.get("sensores", d["sensor_id"])
    disp = repo.get("dispositivos", s["dispositivo_id"]) if s else None
    if not s or not disp:
        raise no_encontrado()
    t = repo.col("tipos_sensor")[s["tipo_sensor_id"]]
    inst = repo.col("instalaciones")[disp["instalacion_id"]]
    from core.util import ms_a_iso
    lote = {"id_sistema": inst["codigo"], "id_dispositivo": disp["codigo"],
            "mediciones": [{"id_sensor": s["codigo"], "tipo_variable": t["tipo_variable"], "valor": d["valor"], "unidad": t["unidad"],
                            "timestamp": ms_a_iso(d.get("timestamp") or ahora_ms())}]}
    r = procesar_lote(disp, lote, origen="manual", actualizar_estado=False)
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="medicion_manual", entidad="sensor",
                                  entidad_id=s["id"], ip=ip_cliente(request))
    invalidar_micro()
    if r["aceptadas"]:
        return JsonResponse(r, status=201)
    if r["duplicadas"]:
        raise conflicto("Ya existe una medición de ese sensor en ese instante.")
    motivo = r["rechazadas"][0]["motivo"] if r["rechazadas"] else "datos inválidos"
    raise ErrorApi(422, "medicion_rechazada", f"La medición fue rechazada: {motivo}.", r["rechazadas"])


#  ALERTAS
@ruta(("GET",))
def alertas(request):
    try:
        limite = min(max(int(request.GET.get("limite") or 100), 1), 500)
    except ValueError:
        limite = 100
    return JsonResponse(_lista_alertas(request.usuario, request.GET.get("instalacion_id"), request.GET.get("estado"), limite), safe=False)


def _lista_alertas(usuario, iid=None, estado=None, limite=100):
    vis = instalaciones_visibles(usuario)
    inst, sens, disp, usr = repo.col("instalaciones"), repo.col("sensores"), repo.col("dispositivos"), repo.col("usuarios")
    cult, tipos, reglas, mantenciones, salida = repo.col("cultivos"), repo.col("tipos_sensor"), repo.col("reglas"), repo.col("mantenciones"), []
    for a in _fresco("alertas", lambda: modulo_alertas.ultimas(500)):
        if (vis is not None and a["instalacion_id"] not in vis) or (iid and a["instalacion_id"] != iid):
            continue
        if estado == "abiertas" and a["estado"] == "resuelta":
            continue
        if estado in ("activa", "reconocida", "revisada", "resuelta") and a["estado"] != estado:
            continue
        if estado == "no_solucionadas" and not (a["estado"] != "resuelta" and a.get("revision_solucionado") is False):
            continue
        salida.append({
            "id": a["id"], "tipo": a["tipo"], "estado": a["estado"], "mensaje": a["mensaje"], "problema": modulo_alertas.problema(a),
            "severidad": a.get("severidad") or ("emergencia" if a["tipo"] == "sin_comunicacion" else "advertencia"), "valor_detectado": a.get("valor_detectado"),
            "creada_en": iso(a["creada_en"]), "reconocida_en": iso(a.get("reconocida_en")), "resuelta_en": iso(a.get("resuelta_en")),
            "instalacion_id": a["instalacion_id"], "instalacion": inst.get(a["instalacion_id"], {}).get("nombre"),
            "sensor_codigo": sens.get(a.get("sensor_id"), {}).get("codigo"),
            "dispositivo_codigo": disp.get(a.get("dispositivo_id"), {}).get("codigo"),
            "cultivo": cult.get(a.get("cultivo_id"), {}).get("nombre"),
            "reconocida_por": usr.get(a.get("reconocida_por"), {}).get("nombre"),
            "normalizada_en": iso(a.get("normalizada_en")),
            "revision_por": usr.get(a.get("revision_por"), {}).get("nombre"), "revision_en": iso(a.get("revision_en")),
            "revision_nota": a.get("revision_nota"), "revision_solucionado": a.get("revision_solucionado"),
            "verificacion_por": usr.get(a.get("verificacion_por"), {}).get("nombre"), "verificacion_en": iso(a.get("verificacion_en")),
            "verificacion_nota": a.get("verificacion_nota"), "verificacion_aprobada": a.get("verificacion_aprobada"),
            "resuelta_auto": bool(a.get("resuelta_auto")), "sensor_id": a.get("sensor_id"),
            "solucion_cuestionada": bool(a.get("solucion_cuestionada")), "requiere_tecnico": bool(a.get("requiere_tecnico")) and a["estado"] != "resuelta",
            "espera_medicion_hasta": iso(a.get("espera_medicion_hasta")) if a.get("estado") == "revisada" else None,
            "mantencion": mantencion_publica({**mantenciones[a["mantencion_id"]], "id": a["mantencion_id"]}, usr) if a.get("mantencion_id") in mantenciones else None,
            "unidad": tipos.get(sens.get(a.get("sensor_id"), {}).get("tipo_sensor_id"), {}).get("unidad"),
            "regla": ({"operador": regla["operador"], "umbral": regla["umbral"], "nombre": regla.get("nombre")}
                      if (regla := reglas.get(a.get("regla_id"))) else None),
            "bitacora": [{"por": "Sistema" if b.get("por") == "sistema" else usr.get(b.get("por"), {}).get("nombre", "—"), "rol": b.get("rol"), "accion": b.get("accion"),
                          "nota": b.get("nota"), "en": iso(b.get("en"))}
                         for b in sorted((a.get("bitacora") or {}).values(), key=lambda x: x.get("en", 0))]})
        if len(salida) >= limite:
            break
    return salida


def _exigir_pendiente(a):
    """Una alerta resuelta (sola o por una persona) ya no admite acciones: se explica por qué en vez de un error genérico."""
    if a["estado"] == "resuelta":
        raise conflicto("Esta alerta ya se resolvió sola: el valor volvió a la normalidad y quedó en el historial. No hace falta revisarla."
                        if a.get("resuelta_auto") else "Esta alerta ya está resuelta.")


def _bitacora(mapa, pk, usuario, accion, nota=""):
    """Bitácora de la alerta: cada acción queda registrada con quién la hizo, su rol y cuándo (no se sobrescribe)."""
    ahora = ahora_ms()
    mapa[f"alertas/{pk}/bitacora/{clave_ms(ahora)}_{secrets.token_hex(2)}"] = {
        "por": usuario["id"], "rol": usuario["rol"], "accion": accion, "nota": nota or "", "en": ahora}


def _reparar_simulacion(alerta):
    """En la demostración, cuando alguien da la solución, el dispositivo simulado se recupera (corte o valor anómalo).
    Con un dispositivo real no hace nada: el valor se normaliza cuando realmente se arregla la causa."""
    try:
        from core.simulacion import simulacion
        d = repo.get("dispositivos", alerta.get("dispositivo_id"))
        if d:
            simulacion.reparar(d["codigo"])
    except Exception:
        pass


@ruta(("POST",), roles=A_CARGO)
def reconocer_alerta(request, pk):
    a = rtdb().get(f"alertas/{pk}")
    if a is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, a["instalacion_id"])
    _exigir_pendiente(a)
    if a["estado"] != "activa":
        raise invalido("Solo se pueden reconocer alertas activas.")
    mapa = {f"alertas/{pk}/estado": "reconocida", f"alertas/{pk}/reconocida_por": request.usuario["id"],
            f"alertas/{pk}/reconocida_en": ahora_ms()}
    _bitacora(mapa, pk, request.usuario, "reconoció", "Fue a revisar.")
    notificaciones.por_reconocida(pk, a, request.usuario, mapa)
    rtdb().update(mapa)
    notificaciones.despachar_push()
    invalidar_micro()
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="alerta_reconocida", entidad="alerta",
                                  entidad_id=pk, ip=ip_cliente(request))
    return sin_contenido()


@ruta(("POST",), roles=A_CARGO)
def revisar_alerta(request, pk):
    """Quien fue a revisar informa qué encontró: «solucionado» o «no solucionado». Ambas respuestas se registran.
    - Encargado: si quedó solucionado, la alerta pasa a «revisada» y espera la confirmación del supervisor o del administrador.
    - Supervisor o administrador (cubren al encargado): quien revisa es también quien da por solucionado, porque no hay nadie por
      encima que lo apruebe; si lo da por solucionado la alerta queda resuelta directamente y la bitácora guarda quién lo hizo.
      Si no se pudo solucionar, queda registrado y la alerta sigue abierta (el propio revisor la rechaza).
    LA MEDICIÓN TIENE LA ÚLTIMA PALABRA: si al informar «solucionado» el dato real sigue mal (el dispositivo no se comunica o el valor
    sigue fuera de la regla), la alerta queda «revisada, esperando la medición» por unos minutos. Si en ese plazo el dato se normaliza,
    se resuelve sola; si no, la solución queda cuestionada y se deriva (ver `monitor.revisar_soluciones`).
    Si el valor vuelve a la normalidad antes de que alguien la revise, la alerta se resuelve sola (ver `alertas.normalizar`)."""
    d = validar(request.cuerpo, {"solucionado": Campo(booleano), "nota": Campo(texto(5, 500))})
    a = rtdb().get(f"alertas/{pk}")
    if a is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, a["instalacion_id"])
    _exigir_pendiente(a)
    if a["estado"] != "reconocida":
        raise invalido("Solo se puede registrar la revisión de una alerta reconocida (primero hay que avisar que se va a revisar).")
    ahora = ahora_ms()
    cambios = {"revision_por": request.usuario["id"], "revision_en": ahora, "revision_nota": d["nota"], "revision_solucionado": d["solucionado"]}
    presente, detalle = causa_presente(a) if d["solucionado"] else (False, "")
    directa = d["solucionado"] and not presente and request.usuario["rol"] in SIN_SUPERIOR
    if directa:                                              # supervisor o administrador: nadie más debe confirmarlo
        cambios.update(estado="resuelta", resuelta_en=ahora, verificacion_por=request.usuario["id"], verificacion_en=ahora,
                       verificacion_aprobada=True,
                       verificacion_nota=f"Resuelta directamente por el {NOMBRE_ROL.get(request.usuario['rol'], 'supervisor')}.")
    elif d["solucionado"]:
        cambios["estado"] = "revisada"                       # falta la constatación de otra persona o de la medición
        if presente:
            cambios["espera_medicion_hasta"] = ahora + modulo_alertas.GRACIA_MEDICION_MS
    if directa or not d["solucionado"]:
        cambios["espera_medicion_hasta"] = None
    mapa = {f"alertas/{pk}/{k}": v for k, v in cambios.items()}
    _bitacora(mapa, pk, request.usuario, "resolvió" if directa else "dio solución" if d["solucionado"] else "no pudo solucionar", d["nota"])
    if d["solucionado"] and presente:
        modulo_alertas.bitacora_sistema(mapa, pk, "esperando la medición",
                                        f"Todavía {detalle}. Si en {modulo_alertas.GRACIA_MEDICION_MS // 60000} minutos la medición no lo refleja, "
                                        "la solución quedará cuestionada.")
    try:
        if directa:
            modulo_alertas.cerrar(pk, mapa)
            notificaciones.por_verificacion(pk, a, request.usuario, True, d["nota"], mapa)     # avisa al encargado y al administrador
        else:
            notificaciones.por_revision(pk, a, request.usuario, d["solucionado"], d["nota"], mapa)     # el administrador y el supervisor ven el comentario
        rtdb().update(mapa)
    except Exception:
        modulo_alertas.reiniciar()
        notificaciones.descartar_push()
        raise
    if d["solucionado"]:
        _reparar_simulacion(a)
    notificaciones.despachar_push()
    invalidar_micro()
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="alerta_revisada", entidad="alerta", entidad_id=pk,
                                  ip=ip_cliente(request), detalle={"solucionado": d["solucionado"], "nota": d["nota"], "directa": directa})
    return sin_contenido()


def causa_presente(a):
    """¿Sigue presente la causa de la alerta AHORA? Se mira el dato real (no solo la marca `normalizada_en`):
    dispositivo en línea para «sin comunicación»; última lectura dentro de la regla para las de umbral."""
    if a.get("tipo") == "sin_comunicacion":
        d = rtdb().get(f"dispositivos/{a.get('dispositivo_id')}") or {}
        return d.get("estado_conexion") != "en_linea", "el dispositivo todavía no vuelve a comunicarse"
    r, u = rtdb().get(f"reglas/{a.get('regla_id')}"), rtdb().get(f"ultima/{a.get('sensor_id')}")
    if not r or not r.get("activa", True) or not u:
        return False, ""
    from core.ingesta import OPERADORES
    if OPERADORES[r["operador"]](u["valor"], r["umbral"]):
        t = repo.col("tipos_sensor").get((repo.get("sensores", a.get("sensor_id")) or {}).get("tipo_sensor_id"), {})
        return True, f"la última lectura ({u['valor']:g} {t.get('unidad', '')}) sigue fuera de la regla ({r['operador']} {r['umbral']:g})"
    return False, ""


@ruta(("POST",), roles=A_CARGO)
def verificar_alerta(request, pk):
    """Ticket de confirmación: quien está a cargo del cultivo (el encargado) constata la solución informada —por el técnico o por otra persona—
    y la confirma (alerta resuelta) o la rechaza (vuelve a revisión). El supervisor y el administrador pueden hacerlo como respaldo.
    Otra persona debe constatarla; la única excepción es el administrador, que puede confirmar o rechazar su propia revisión."""
    d = validar(request.cuerpo, {"aprobada": Campo(booleano), "nota": Campo(texto(0, 500), requerido=False)})
    nota = d.get("nota", "")
    if not d["aprobada"] and len(nota) < 5:
        raise invalido("Para rechazar la revisión indica el motivo.", [{"campo": "nota", "mensaje": "Es obligatoria al rechazar."}])
    a = rtdb().get(f"alertas/{pk}")
    if a is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, a["instalacion_id"])
    _exigir_pendiente(a)
    if a["estado"] != "revisada":
        raise invalido("Solo se puede verificar una alerta que ya fue revisada.")
    # Regla: quien hizo la revisión no se confirma a sí mismo, salvo el administrador y el cliente particular (no hay nadie por encima que la constate).
    autoconfirma = a.get("revision_por") == request.usuario["id"]
    if autoconfirma and request.usuario["rol"] not in ("administrador", "cliente"):
        raise ErrorApi(403, "misma_persona", "Quien hizo la revisión no puede confirmarla: debe constatarla otra persona.")
    ahora = ahora_ms()
    if autoconfirma:                                         # queda a la vista que el administrador se constató a sí mismo
        nota = (nota + " " if nota else "") + f"(constatado por el propio {NOMBRE_ROL.get(request.usuario['rol'], 'administrador')})"
    cambios = {"verificacion_por": request.usuario["id"], "verificacion_en": ahora, "verificacion_nota": nota, "verificacion_aprobada": d["aprobada"]}
    if d["aprobada"]:
        presente, detalle = causa_presente(a)
        if presente:
            raise conflicto(f"Todavía no se puede confirmar: {detalle}. Espera unos segundos a que el dispositivo informe el valor normal "
                            "y confirma de nuevo, o rechaza la solución para que se revise otra vez.")
        if not a.get("normalizada_en"):
            cambios["normalizada_en"] = ahora
        cambios.update(estado="resuelta", resuelta_en=ahora)
    else:
        cambios["estado"] = "reconocida"                     # vuelve a revisión
        cambios["espera_medicion_hasta"] = None
    mapa = {f"alertas/{pk}/{k}": v for k, v in cambios.items()}
    _bitacora(mapa, pk, request.usuario, ("confirmó su propia solución" if autoconfirma else "confirmó la solución") if d["aprobada"]
              else ("rechazó su propia solución" if autoconfirma else "rechazó la solución"), nota)
    cuestionada = None
    try:
        if d["aprobada"]:
            modulo_alertas.cerrar(pk, mapa)
        elif a.get("revision_por") and a.get("revision_por") != request.usuario["id"]:       # otra persona dijo «solucionado» y no era cierto
            nombre = repo.col("usuarios").get(a["revision_por"], {}).get("nombre", "Quien revisó")
            tecnico = a.get("tipo") == "sin_comunicacion" or bool(a.get("cuestionadas"))
            cuestionada = f"{nombre} informó que quedó solucionado, pero {request.usuario['nombre']} lo rechazó: {nota}"
            modulo_alertas.cuestionar(pk, a["revision_por"], cuestionada, mapa, requiere_tecnico=tecnico)
            notificaciones.por_cuestionada(pk, a, cuestionada, tecnico, mapa)
        notificaciones.por_verificacion(pk, a, request.usuario, d["aprobada"], nota, mapa)
        rtdb().update(mapa)
    except Exception:
        modulo_alertas.reiniciar()
        notificaciones.descartar_push()
        raise
    notificaciones.despachar_push()
    invalidar_micro()
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="alerta_verificada" if d["aprobada"] else "alerta_rechazada",
                                  entidad="alerta", entidad_id=pk, ip=ip_cliente(request), detalle={"nota": nota, "autoconfirmada": autoconfirma})
    if cuestionada:
        seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="solucion_cuestionada", entidad="alerta", entidad_id=pk,
                                      ip=ip_cliente(request), detalle={"persona": a.get("revision_por"), "motivo": cuestionada})
    return sin_contenido()


#  EVENTOS
@ruta(("GET",))
def eventos(request):
    vis, iid = instalaciones_visibles(request.usuario), request.GET.get("instalacion_id")
    inst, disp = repo.col("instalaciones"), repo.col("dispositivos")
    salida = []
    for k, e in reversed(rtdb().rango("eventos", ultimos=400)):
        if (vis is not None and e["instalacion_id"] not in vis) or (iid and e["instalacion_id"] != iid):
            continue
        salida.append({"id": k, "tipo": e["tipo"], "descripcion": e["descripcion"], "ocurrido_en": iso(e["ocurrido_en"]),
                       "instalacion": inst.get(e["instalacion_id"], {}).get("nombre"),
                       "dispositivo_codigo": disp.get(e.get("dispositivo_id"), {}).get("codigo")})
    return JsonResponse(salida[:100], safe=False)


#  PANEL
def _mediciones_24h(iid):
    desde = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y%m%d%H")
    return sum(v for k, v in rtdb().rango(f"contadores/{iid}", desde=desde) if isinstance(v, (int, float)) and k > desde)


@ruta(("GET",))
def panel(request):
    return JsonResponse(_panel(request.usuario))


def _panel(usuario):
    vis = instalaciones_visibles(usuario)
    disp, filas = repo.lista("dispositivos"), []
    todas = _fresco("alertas", lambda: modulo_alertas.ultimas(500))
    abiertas = [a for a in todas if a["estado"] != "resuelta"]
    usr, desde24 = repo.col("usuarios"), ahora_ms() - 24 * 3600 * 1000

    def estado_alerta(did):                                 # lo más urgente que tiene abierto el dispositivo
        est = {a["estado"] for a in abiertas if a.get("dispositivo_id") == did}
        return next((e for e in ("activa", "reconocida", "revisada") if e in est), "ok")

    def ultima_solucion(did):                               # la última alerta que se resolvió en las últimas 24 h
        res = [a for a in todas if a.get("dispositivo_id") == did and a["estado"] == "resuelta" and (a.get("resuelta_en") or 0) > desde24]
        if not res:
            return None
        a = max(res, key=lambda x: x.get("resuelta_en") or 0)
        return {"en": iso(a["resuelta_en"]), "por": usr.get(a.get("verificacion_por"), {}).get("nombre"), "mensaje": a.get("mensaje"),
                "problema": modulo_alertas.problema(a), "solucion_por": usr.get(a.get("revision_por"), {}).get("nombre"),
                "nota": a.get("revision_nota"), "auto": bool(a.get("resuelta_auto"))}

    def fallas(did):                                        # fallas abiertas del dispositivo, la más urgente primero
        orden = {"activa": 0, "reconocida": 1, "revisada": 2}
        lista = sorted((a for a in abiertas if a.get("dispositivo_id") == did), key=lambda a: (orden.get(a["estado"], 3), -a.get("creada_en", 0)))
        return [{"id": a["id"], "problema": modulo_alertas.problema(a), "estado": a["estado"], "creada_en": iso(a.get("creada_en")),
                 "severidad": a.get("severidad") or "advertencia",
                 "por": usr.get(a.get("revision_por") or a.get("reconocida_por"), {}).get("nombre"),
                 "no_solucionado": a.get("revision_solucionado") is False} for a in lista[:5]]
    for i in sorted(repo.lista("instalaciones"), key=lambda x: x["nombre"].lower()):
        if vis is not None and i["id"] not in vis:
            continue
        activos = [d for d in disp if d["instalacion_id"] == i["id"] and d.get("activo", True)]
        cult = repo.col("cultivos")
        filas.append({
            "cultivos": [{"id": d.get("cultivo_id"), "nombre": cult.get(d.get("cultivo_id"), {}).get("nombre", "Sin cultivo"),
                          "dispositivo_codigo": d["codigo"], "dispositivo_id": d["id"], "estado_conexion": d.get("estado_conexion", "sin_datos"),
                          "alertas_abiertas": sum(a.get("dispositivo_id") == d["id"] for a in abiertas),
                          "estado_alerta": estado_alerta(d["id"]), "ultima_solucion": ultima_solucion(d["id"]), "fallas": fallas(d["id"])}
                         for d in sorted(activos, key=lambda x: x["codigo"])],
            "id": i["id"], "codigo": i["codigo"], "nombre": i["nombre"], "activa": i.get("activa", True),
            "dispositivos": len(activos), "en_linea": sum(d.get("estado_conexion") == "en_linea" for d in activos),
            "sin_comunicacion": sum(d.get("estado_conexion") == "sin_comunicacion" for d in activos),
            "alertas_abiertas": sum(a["instalacion_id"] == i["id"] for a in abiertas),
            "mediciones_24h": _fresco(f"m24:{i['id']}", lambda i=i: _mediciones_24h(i["id"]), 5)})
    suma = lambda k: sum(x[k] for x in filas)  # noqa: E731
    return {"totales": {"instalaciones": len(filas), "dispositivos": suma("dispositivos"), "en_linea": suma("en_linea"),
                        "sin_comunicacion": suma("sin_comunicacion"), "alertas_abiertas": suma("alertas_abiertas"),
                        "mediciones_24h": suma("mediciones_24h")},
            "instalaciones": filas, "modo_bd": rtdb().modo, "ahora": ahora_ms()}


@ruta(("GET",))
def resumen_movil(request):
    """Todo lo que la app móvil necesita para refrescar en UNA petición (panel, alertas abiertas y lecturas por instalación).
    A través de Firebase cada petición es un viaje de ida y vuelta: juntar tres en una hace la app mucho más ágil."""
    p = _panel(request.usuario)
    return JsonResponse({"panel": p, "abiertas": _lista_alertas(request.usuario, estado="abiertas", limite=30),
                         "estados": [_estado_instalacion(i["id"]) for i in p["instalaciones"]]})


#  COMPARACIÓN (supervisor y administrador)
@ruta(("GET",), roles=("supervisor", "administrador"))
def comparacion(request):
    tv = request.GET.get("tipo_variable") or ""
    if not re.fullmatch(r"[a-z0-9_]{2,40}", tv):
        raise invalido("tipo_variable es obligatorio.")
    desde, hasta = _rango(request.GET)
    bucket = _elegir_bucket(desde, hasta, "auto")
    por_cultivo = request.GET.get("agrupar", "cultivo") == "cultivo" # un dispositivo por cultivo: se compara cada cultivo
    tipos, disp, inst, cult = repo.col("tipos_sensor"), repo.col("dispositivos"), repo.col("instalaciones"), repo.col("cultivos")
    grupos, unidades, nombres = defaultdict(list), {}, {}
    vis = instalaciones_visibles(request.usuario)                  # el supervisor no ve los equipos de clientes particulares
    for s in repo.lista("sensores"):
        t, d = tipos.get(s["tipo_sensor_id"]), disp.get(s["dispositivo_id"])
        if not t or not d or t["tipo_variable"] != tv or (vis is not None and d["instalacion_id"] not in vis):
            continue
        clave = d.get("cultivo_id") if por_cultivo and d.get("cultivo_id") else d["instalacion_id"]
        unidades[clave] = t["unidad"]
        nombres[clave] = (f"{cult[clave]['nombre']} · {d['codigo']} ({inst[d['instalacion_id']]['nombre']})"
                          if clave in cult else inst[d["instalacion_id"]]["nombre"])
        grupos[clave] += [(ms, v["v"]) for ms, v in _leer_mediciones(s["id"], desde, hasta)]
    salida = []
    for clave in sorted(unidades, key=lambda k: nombres[k].lower()):
        datos = sorted(grupos.get(clave, []))
        valores = [v for _, v in datos]
        salida.append({"id": clave, "nombre": nombres[clave],
                       "promedio": sum(valores) / len(valores) if valores else None,
                       "minimo": min(valores) if valores else None, "maximo": max(valores) if valores else None,
                       "n": len(valores), "unidad": unidades[clave],
                       "serie": [{"t": p["t"], "prom": p["prom"]} for p in _agregar(datos, bucket)]})
    return JsonResponse({"tipo_variable": tv, "desde": iso(desde), "hasta": iso(hasta), "bucket_segundos": bucket,
                         "agrupado_por": "cultivo" if por_cultivo else "instalacion", "instalaciones": salida})


# AUDITORÍA (solo administrador)
@ruta(("GET",), roles=("administrador",))
def auditoria(request):
    emails = {k: u["email"] for k, u in repo.col("usuarios").items()}
    import json
    filas = list(reversed(rtdb().rango("auditoria", ultimos=200)))
    return JsonResponse([{
        "id": k, "ocurrido_en": iso(a["ts"]), "usuario": emails.get(a.get("usuario_id")) or None, "accion": a["accion"],
        "entidad": a.get("entidad") or None, "entidad_id": a.get("entidad_id") or None, "ip": a.get("ip") or None,
        "detalle": json.loads(a["detalle"]) if a.get("detalle") else None} for k, a in filas], safe=False)


@ruta(("GET",), roles=("administrador",))
def verificar_auditoria(request):
    return JsonResponse(seguridad.verificar_cadena_auditoria())
