"""Solicitudes técnicas: derivar una falla (o pedir una mantención, un retiro o un reemplazo) a un técnico o servicio técnico.

Sirve igual para una hacienda que para un hogar: la instalación puede tener un técnico asignado («conexión directa») y la solicitud le
llega solo a él; si no tiene, la solicitud queda en la bolsa de todos los técnicos y el primero que la acepta se la queda.
Estados: pendiente -> en curso (aceptada) -> realizada | cancelada. El técnico también puede devolverla a la bolsa.
Cada paso queda en la bitácora de la alerta (si la hay), en los avisos y en la auditoría, y alimenta los informes.
"""
import secrets

from django.http import JsonResponse

from core import cache_micro, notificaciones, repo, seguridad
from core.http import (Campo, conflicto, exigir_acceso_instalacion, instalaciones_visibles, invalido, ip_cliente, no_encontrado, opcion,
                       prohibido, ruta, sin_contenido, texto, validar, entero)
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms, iso

TIPOS = {"tecnico": "Visita de técnico", "bateria": "Cambio de batería", "dispositivo": "Cambio de dispositivo",
         "configuracion": "Falla de configuración", "componente": "Falla de un componente", "sensor": "Revisión o cambio de sensor",
         "red": "Revisión de red, WiFi o energía", "otro": "Otra mantención"}
ACCIONES = {"arreglo": "Arreglo o reparación", "reemplazo": "Reemplazo del equipo", "retiro": "Retiro del equipo", "mantencion": "Mantención preventiva"}
RESOLUCIONES = {"arreglado": "Arreglado", "reemplazado": "Reemplazado", "retirado": "Retirado", "mantencion_realizada": "Mantención realizada",
                "sin_solucion": "No se pudo resolver"}
ESTADOS = {"pendiente": "Pendiente", "en_curso": "En curso", "completada": "Realizada", "cancelada": "Cancelada"}
ABIERTAS = ("pendiente", "en_curso")
ROLES_SOLICITANTES = ("encargado_cultivo", "supervisor", "administrador")


def _tecnico_valido(tid):
    u = repo.get("usuarios", tid)
    if not u or u.get("rol") != "tecnico" or not u.get("activo", True) or u.get("eliminado"):
        raise invalido("El técnico indicado no existe o está suspendido.")
    return u


def mantencion_publica(m, usuarios=None, detalle=False):
    """Vista de una orden. `detalle` agrega lo que necesita el técnico para ir: dónde está el equipo y con quién hablar."""
    if not m:
        return None
    usr = usuarios if usuarios is not None else repo.col("usuarios")
    nombre = lambda uid: usr.get(uid, {}).get("nombre")  # noqa: E731
    sal = {"id": m.get("id"), "alerta_id": m.get("alerta_id") or None, "tipo": m["tipo"], "tipo_texto": TIPOS.get(m["tipo"], m["tipo"]),
           "accion": m.get("accion") or "arreglo", "accion_texto": ACCIONES.get(m.get("accion") or "arreglo"),
           "estado": m["estado"], "estado_texto": ESTADOS.get(m["estado"], m["estado"]), "nota": m.get("nota"), "tecnico": m.get("tecnico") or None,
           "tecnico_id": m.get("tecnico_id") or None, "tecnico_nombre": nombre(m.get("tecnico_id")),
           "creada_en": iso(m.get("creada_en")), "creada_por": nombre(m.get("creada_por")), "aceptada_en": iso(m.get("aceptada_en")),
           "completada_en": iso(m.get("completada_en")), "completada_por": nombre(m.get("completada_por")), "resultado": m.get("resultado"),
           "resolucion": m.get("resolucion"), "resolucion_texto": RESOLUCIONES.get(m.get("resolucion")), "repuestos": m.get("repuestos"),
           "progreso": m.get("progreso") or ("aceptada" if m["estado"] == "en_curso" else None), "en_camino_en": iso(m.get("en_camino_en")),
           "en_sitio_en": iso(m.get("en_sitio_en")), "diagnostico": m.get("diagnostico"), "recomendacion": m.get("recomendacion"),
           "minutos": m.get("minutos")}
    if detalle:
        inst, disp, cult = repo.col("instalaciones").get(m.get("instalacion_id"), {}), repo.col("dispositivos").get(m.get("dispositivo_id"), {}), repo.col("cultivos").get(m.get("cultivo_id"), {})
        sol = usr.get(m.get("creada_por"), {})
        sal.update(instalacion_id=m.get("instalacion_id"), instalacion=inst.get("nombre"), direccion=inst.get("ubicacion"), cultivo=cult.get("nombre"),
                   dispositivo_id=m.get("dispositivo_id"), dispositivo=disp.get("codigo"), dispositivo_nombre=disp.get("nombre"),
                   estado_conexion=disp.get("estado_conexion"), ubicacion=_ubicacion_publica(disp.get("ubicacion")),
                   solicitante={"nombre": sol.get("nombre"), "email": sol.get("email"), "rol": sol.get("rol")})
        if m.get("alerta_id"):                                    # el porqué: qué alerta originó la visita
            al = rtdb().get(f"alertas/{m['alerta_id']}") or {}
            sal["alerta_info"] = {"id": m["alerta_id"], "mensaje": al.get("mensaje"), "severidad": al.get("severidad"), "tipo": al.get("tipo"),
                             "estado": al.get("estado"), "problema": al.get("problema"), "valor": al.get("valor_detectado")}
    return sal


def _ubicacion_publica(u):
    if not u:
        return None
    return {"lat": u["lat"], "lng": u["lng"], "precision": u.get("precision"), "origen": u.get("origen"), "actualizada_en": iso(u.get("actualizada_en"))}


def _crear_orden(request, *, instalacion_id, dispositivo_id, cultivo_id, alerta, tipo, accion, nota, tecnico_txt, tecnico_id):
    """Crea la orden y devuelve (id, mapa). El mapa trae también los avisos; el llamador lo escribe de una vez."""
    ahora, mid, quien = ahora_ms(), repo.nuevo_id(), request.usuario
    if not tecnico_id:                                            # conexión directa: el técnico asignado a la instalación
        tecnico_id = repo.col("instalaciones").get(instalacion_id, {}).get("tecnico_id") or ""
    if tecnico_id:
        _tecnico_valido(tecnico_id)
    orden = {"alerta_id": alerta or "", "instalacion_id": instalacion_id, "dispositivo_id": dispositivo_id or "", "cultivo_id": cultivo_id or "",
             "tipo": tipo, "accion": accion, "nota": nota, "tecnico": tecnico_txt or "", "tecnico_id": tecnico_id or "", "estado": "pendiente",
             "creada_por": quien["id"], "creada_en": ahora}
    mapa = {f"mantenciones/{mid}": orden}
    disp = repo.col("dispositivos").get(dispositivo_id, {})
    donde = f"{repo.col('cultivos').get(cultivo_id, {}).get('nombre', '')} · {disp.get('codigo', '')}".strip(" ·")
    destino = notificaciones.ids_tecnicos(tecnico_id or None, excluir=quien["id"])
    notificaciones.avisar(mapa, destino, "solicitud_tecnica", "Nueva solicitud técnica",
                          f"{donde} — {ACCIONES[accion]} ({TIPOS[tipo]}). {nota}".strip(" —"), alerta or "", instalacion_id, critica=bool(alerta), mantencion_id=mid)
    jefes = [u["id"] for u in repo.lista("usuarios") if u.get("rol") in ("supervisor", "administrador") and u.get("activo", True) and not u.get("eliminado")
             and u["id"] != quien["id"] and u["id"] not in destino]
    notificaciones.avisar(mapa, jefes, "alerta_derivada", "Derivada a técnico / mantención",
                          f"{donde} — {quien['nombre']} pidió: {ACCIONES[accion]} ({TIPOS[tipo]}). {nota}".strip(" —"), alerta or "", instalacion_id, mantencion_id=mid)
    if alerta:
        mapa[f"alertas/{alerta}/mantencion_id"] = mid
        mapa[f"alertas/{alerta}/requiere_tecnico"] = False
        mapa[f"alertas/{alerta}/bitacora/{clave_ms(ahora)}_{secrets.token_hex(2)}"] = {
            "por": quien["id"], "rol": quien["rol"], "accion": "derivó a mantención", "en": ahora,
            "nota": f"{TIPOS[tipo]} · {ACCIONES[accion]}" + (f" ({tecnico_txt})" if tecnico_txt else "") + f": {nota}"}
    return mid, mapa


_ESQUEMA_ORDEN = {"tipo": Campo(opcion(*TIPOS)), "accion": Campo(opcion(*ACCIONES), requerido=False), "nota": Campo(texto(5, 500)),
                  "tecnico": Campo(texto(0, 120), requerido=False), "tecnico_id": Campo(texto(0, 64), requerido=False)}


@ruta(("POST",), roles=ROLES_SOLICITANTES)
def derivar_alerta(request, pk):
    """Deriva una alerta abierta a un técnico o a una mantención."""
    d = validar(request.cuerpo, _ESQUEMA_ORDEN)
    a = rtdb().get(f"alertas/{pk}")
    if a is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, a["instalacion_id"])
    if a["estado"] == "resuelta":
        raise conflicto("Esta alerta ya está resuelta: no necesita mantención.")
    previa = repo.get("mantenciones", a.get("mantencion_id")) if a.get("mantencion_id") else None
    if previa and previa["estado"] in ABIERTAS:
        raise conflicto("Esta alerta ya tiene una mantención en curso. Registra su resultado antes de derivarla otra vez.")
    mid, mapa = _crear_orden(request, instalacion_id=a["instalacion_id"], dispositivo_id=a.get("dispositivo_id"), cultivo_id=a.get("cultivo_id"), alerta=pk,
                             tipo=d["tipo"], accion=d.get("accion", "arreglo"), nota=d["nota"], tecnico_txt=d.get("tecnico"), tecnico_id=d.get("tecnico_id"))
    return _guardar(request, mapa, mid, "alerta_derivada", "alerta", pk, {"tipo": d["tipo"], "mantencion": mid})


@ruta(("GET", "POST"))
def mantenciones(request):
    if request.method == "POST":
        return _solicitud_directa(request)
    uid, rol = request.usuario["id"], request.usuario["rol"]
    vis, iid, estado, tecnico = instalaciones_visibles(request.usuario), request.GET.get("instalacion_id"), request.GET.get("estado"), request.GET.get("tecnico_id")
    usr, salida = repo.col("usuarios"), []
    for m in repo.lista("mantenciones"):
        if rol == "tecnico":                                      # solo lo suyo y la bolsa de solicitudes sin asignar
            if m.get("tecnico_id") != uid and not (not m.get("tecnico_id") and m["estado"] == "pendiente"):
                continue
        elif (vis is not None and m["instalacion_id"] not in vis) or (iid and m["instalacion_id"] != iid):
            continue
        if tecnico and m.get("tecnico_id") != tecnico:
            continue
        if estado == "abiertas" and m["estado"] not in ABIERTAS or estado in ESTADOS and m["estado"] != estado:
            continue
        pub = mantencion_publica(m, usr, detalle=True)
        pub["en_bolsa"] = not m.get("tecnico_id") and m["estado"] == "pendiente"
        salida.append(pub)
    for m in salida:
        if m["alerta_id"] and m["estado"] in ABIERTAS:
            m["alerta"] = (m.get("alerta_info") or {}).get("mensaje")
    return JsonResponse(sorted(salida, key=lambda x: x["creada_en"] or "", reverse=True), safe=False)


def _solicitud_directa(request):
    """Solicitud sin alerta previa: mantención preventiva, retiro o reemplazo de un equipo (útil también en un hogar)."""
    if request.usuario["rol"] not in ROLES_SOLICITANTES:
        raise prohibido()
    d = validar(request.cuerpo, {"dispositivo_id": Campo(texto(1, 64)), **_ESQUEMA_ORDEN})
    disp = repo.get("dispositivos", d["dispositivo_id"])
    if disp is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, disp["instalacion_id"])
    mid, mapa = _crear_orden(request, instalacion_id=disp["instalacion_id"], dispositivo_id=disp["id"], cultivo_id=disp.get("cultivo_id"), alerta=None,
                             tipo=d["tipo"], accion=d.get("accion", "mantencion"), nota=d["nota"], tecnico_txt=d.get("tecnico"), tecnico_id=d.get("tecnico_id"))
    return _guardar(request, mapa, mid, "solicitud_tecnica_creada", "dispositivo", disp["id"], {"tipo": d["tipo"], "mantencion": mid})


def _guardar(request, mapa, mid, accion, entidad, entidad_id, detalle):
    try:
        rtdb().update(mapa)
    except Exception:
        notificaciones.descartar_push()
        raise
    repo.invalidar("mantenciones")
    notificaciones.despachar_push()
    cache_micro.limpiar()
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion=accion, entidad=entidad, entidad_id=entidad_id, ip=ip_cliente(request), detalle=detalle)
    return JsonResponse({"id": mid}, status=201)


@ruta(("GET",), roles=ROLES_SOLICITANTES)
def tecnicos(request):
    """Técnicos disponibles para pedirles una mantención directamente."""
    return JsonResponse(sorted([{"id": u["id"], "nombre": u["nombre"], "email": u.get("email")} for u in repo.lista("usuarios")
                                if u.get("rol") == "tecnico" and u.get("activo", True) and not u.get("eliminado")], key=lambda x: x["nombre"].lower()), safe=False)


@ruta(("PATCH",), roles=("tecnico", "supervisor", "administrador"))
def mantencion_detalle(request, pk):
    """El técnico acepta, devuelve o cierra su solicitud; el supervisor y el administrador además asignan, cancelan o cierran cualquiera."""
    d = validar(request.cuerpo, {"estado": Campo(opcion("en_curso", "completada", "cancelada", "devuelta")), "nota": Campo(texto(0, 500)), "tecnico": Campo(texto(0, 120)),
                                 "tecnico_id": Campo(texto(0, 64)), "resolucion": Campo(opcion(*RESOLUCIONES)), "repuestos": Campo(texto(0, 300)),
                                 "progreso": Campo(opcion("en_camino", "en_sitio")), "diagnostico": Campo(texto(0, 600)), "recomendacion": Campo(texto(0, 500)),
                                 "minutos": Campo(entero(0, 1440))}, parcial=True)
    m = repo.get("mantenciones", pk)
    if m is None:
        raise no_encontrado()
    quien, es_tec = request.usuario, request.usuario["rol"] == "tecnico"
    if es_tec:
        if m.get("tecnico_id") and m["tecnico_id"] != quien["id"]:
            raise no_encontrado()
        if d.get("estado") == "cancelada" or "tecnico_id" in d:
            raise prohibido()
    else:
        exigir_acceso_instalacion(quien, m["instalacion_id"])
    if m["estado"] not in ABIERTAS:
        raise conflicto("Esta mantención ya está cerrada.")
    nota, estado, ahora = d.get("nota", ""), d.get("estado"), ahora_ms()
    if estado in ("completada", "cancelada", "devuelta") and len(nota) < 5:
        raise invalido("Cuenta qué se hizo (o por qué se cancela o se devuelve).", [{"campo": "nota", "mensaje": "Es obligatoria."}])
    if estado == "completada" and not d.get("resolucion"):
        raise invalido("Indica cómo quedó: arreglado, reemplazado, retirado, mantención realizada o no se pudo resolver.", [{"campo": "resolucion", "mensaje": "Es obligatoria."}])
    cambios, accion = {}, None
    usr = repo.col("usuarios")
    if d.get("tecnico"):
        cambios["tecnico"] = d["tecnico"]
    if "tecnico_id" in d and not es_tec:
        if d["tecnico_id"]:
            _tecnico_valido(d["tecnico_id"])
        cambios["tecnico_id"] = d["tecnico_id"] or ""
        accion = f"asignó la mantención a {usr.get(d['tecnico_id'], {}).get('nombre')}" if d["tecnico_id"] else "devolvió la mantención a la bolsa de técnicos"
    if estado == "en_curso":
        cambios["estado"] = "en_curso"
        if not m.get("aceptada_en"):
            cambios["aceptada_en"] = ahora
        if es_tec and not m.get("tecnico_id"):
            cambios["tecnico_id"] = quien["id"]
        accion = "aceptó la mantención" if es_tec else "puso la mantención en curso"
    elif d.get("progreso"):                                       # «voy en camino» / «ya llegué»: el solicitante sabe dónde va la visita
        if not es_tec or m["estado"] != "en_curso":
            raise conflicto("Primero acepta la solicitud; después puedes avisar que vas en camino o que ya llegaste.")
        cambios["progreso"] = d["progreso"]
        cambios["en_camino_en" if d["progreso"] == "en_camino" else "en_sitio_en"] = ahora
        accion = "va en camino al equipo" if d["progreso"] == "en_camino" else "llegó al equipo"
    elif estado == "devuelta":
        cambios.update(estado="pendiente", tecnico_id="", aceptada_en=None, progreso=None, en_camino_en=None, en_sitio_en=None)
        accion = "devolvió la mantención a la bolsa de técnicos"
    elif estado in ("completada", "cancelada"):
        cambios.update(estado=estado, resultado=nota, completada_en=ahora, completada_por=quien["id"])
        if d.get("resolucion"):
            cambios["resolucion"] = d["resolucion"]
        if d.get("repuestos"):
            cambios["repuestos"] = d["repuestos"]
        for campo in ("diagnostico", "recomendacion"):             # reporte de la visita
            if d.get(campo):
                cambios[campo] = d[campo]
        if d.get("minutos") is not None:
            cambios["minutos"] = d["minutos"]
        accion = "registró la mantención como realizada" if estado == "completada" else "canceló la mantención"
    if not cambios:
        raise invalido("No hay cambios que aplicar.")
    mapa = {f"mantenciones/{pk}/{k}": v for k, v in cambios.items()}
    if m.get("alerta_id") and accion:
        mapa[f"alertas/{m['alerta_id']}/bitacora/{clave_ms(ahora)}_{secrets.token_hex(2)}"] = {
            "por": quien["id"], "rol": quien["rol"], "accion": accion,
            "nota": (f"Diagnóstico: {d['diagnostico']}. " if d.get("diagnostico") and estado == "completada" else "") + nota, "en": ahora}
    if accion:                                                    # quien pidió la mantención y los jefes se enteran
        interesados = [m.get("creada_por")] + [u["id"] for u in repo.lista("usuarios") if u.get("rol") in ("supervisor", "administrador") and u.get("activo", True)]
        notificaciones.avisar(mapa, [x for x in interesados if x and x != quien["id"]], "mantencion_actualizada", "Solicitud técnica actualizada",
                              f"{quien['nombre']} {accion}. {nota}".strip(), m.get("alerta_id") or "", m["instalacion_id"], mantencion_id=pk)
    try:
        rtdb().update(mapa)
    except Exception:
        notificaciones.descartar_push()
        raise
    repo.invalidar("mantenciones")
    notificaciones.despachar_push()
    cache_micro.limpiar()
    seguridad.registrar_auditoria(usuario_id=quien["id"], accion="mantencion_actualizada", entidad="mantencion", entidad_id=pk, ip=ip_cliente(request),
                                  detalle={"estado": estado, "resolucion": d.get("resolucion")})
    return sin_contenido()
