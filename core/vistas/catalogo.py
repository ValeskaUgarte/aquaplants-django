"""Catálogo: usuarios, instalaciones, cultivos, plantas, tipos de sensor, dispositivos, sensores, reglas y parámetros.
Mismos permisos que el proyecto original: lectura según alcance del usuario; escritura solo del administrador."""
from django.http import JsonResponse

from core import repo, seguridad
from core.http import (Campo, ErrorApi, booleano, conflicto, correo, entero, id_valor, instalaciones_visibles, invalido,
                       ip_cliente, lista_ids, no_encontrado, numero, opcion, prohibido, ruta, sin_contenido, texto, validar)
from core.rtdb import rtdb
from core.util import ahora_ms, iso

ADMIN = ("administrador",)
ROLES = opcion("administrador", "supervisor", "encargado_cultivo", "tecnico", "cliente")
_VARIABLE = r"[a-z0-9_]{2,40}"
_OPERADORES = opcion(">", ">=", "<", "<=")
_SEVERIDAD = opcion("advertencia", "emergencia")


def _audit(request, accion, entidad, entidad_id=None, detalle=None):
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion=accion, entidad=entidad, entidad_id=entidad_id,
                                  ip=ip_cliente(request), detalle=detalle)


def _politica(password, email, nombre):
    errs = seguridad.validar_politica_password(password, email, nombre)
    if errs:
        raise invalido("La contraseña no cumple la política.", [{"campo": "password", "mensaje": m} for m in errs])


def _visible(iid, vis):
    return vis is None or iid in vis


def _solo_admin(request):
    if request.usuario["rol"] != "administrador":
        raise prohibido()


def _existe(coleccion, id_):
    if id_ not in repo.col(coleccion):
        raise ErrorApi(409, "referencia_invalida", "El registro referenciado no existe o está en uso.")


def _instalacion_de_dispositivo(did):
    d = repo.col("dispositivos").get(did)
    return d["instalacion_id"] if d else None


# ============================================================ USUARIOS (solo administrador)
@ruta(("GET", "POST"), roles=ADMIN)
def usuarios(request):
    if request.method == "GET":
        return JsonResponse([{
            "id": u["id"], "email": u["email"], "nombre": u["nombre"], "rol": u["rol"], "activo": u.get("activo", True),
            "totp_activo": bool(u.get("totp_activo")), "ultimo_login": iso(u.get("ultimo_login")),
            "bloqueado_hasta": iso(u.get("bloqueado_hasta")), "instalaciones": sorted((u.get("instalaciones") or {}).keys()),
            "dispositivos": sorted((u.get("dispositivos") or {}).keys()), "creado_en": iso(u.get("creado_en")),
            "suspension": ({"motivo": u.get("suspension_motivo"), "en": iso(u.get("suspendido_en")), "por": repo.col("usuarios").get(u.get("suspendido_por"), {}).get("nombre")}
                           if not u.get("activo", True) else None),
        } for u in sorted(repo.lista("usuarios"), key=lambda x: x["nombre"].lower()) if not u.get("eliminado")], safe=False)
    d = validar(request.cuerpo, {"email": Campo(correo), "nombre": Campo(texto(2, 120)), "password": Campo(texto(0, 128)),
                                 "rol": Campo(ROLES), "instalaciones": Campo(lista_ids(200), requerido=False), "dispositivos": Campo(lista_ids(500), requerido=False)})
    _politica(d["password"], d["email"], d["nombre"])
    if repo.usuario_por_email(d["email"]):
        raise conflicto("Ya existe un usuario con ese correo.")
    for i in d.get("instalaciones", []):
        if i not in repo.col("instalaciones"):
            raise invalido("Una de las instalaciones indicadas no existe.")
    insts = _con_instalaciones_de(d.get("instalaciones", []), d.get("dispositivos", []))
    uid = repo.crear_usuario(d["email"], d["nombre"], d["rol"], seguridad.hash_password(d["password"]), insts)
    if d.get("dispositivos"):
        repo.actualizar("usuarios", uid, {"dispositivos": {x: True for x in d["dispositivos"]}})
    _audit(request, "usuario_creado", "usuario", uid, {"rol": d["rol"]})
    return JsonResponse({"id": uid}, status=201)


def _con_instalaciones_de(instalaciones, dispositivos):
    """Vincular a alguien con un dispositivo le da también acceso a su instalación. Valida que todo exista."""
    disp = repo.col("dispositivos")
    for x in dispositivos:
        if x not in disp:
            raise invalido("Alguno de los dispositivos indicados no existe.")
    return sorted(set(instalaciones) | {disp[x]["instalacion_id"] for x in dispositivos})


def _eliminar_usuario(request, u, pk):
    """Elimina la cuenta: no puede volver a entrar y su correo queda libre, pero se conserva el nombre en el historial (bitácoras, informes)."""
    if pk == request.usuario["id"]:
        raise conflicto("No puedes eliminar tu propia cuenta.")
    if u.get("rol") == "administrador" and sum(1 for x in repo.lista("usuarios") if x.get("rol") == "administrador" and x.get("activo", True) and not x.get("eliminado")) <= 1:
        raise conflicto("Es el único administrador activo: crea otro antes de eliminarlo.")
    abiertas = [m for m in repo.lista("mantenciones") if m.get("tecnico_id") == pk and m.get("estado") in ("pendiente", "en_curso")]
    mapa = {f"usuarios/{pk}/eliminado": True, f"usuarios/{pk}/eliminado_en": ahora_ms(), f"usuarios/{pk}/activo": False,
            f"usuarios/{pk}/instalaciones": None, f"usuarios/{pk}/dispositivos": None, f"indice_email/{repo.clave_email(u['email'])}": None}
    for m in abiertas:                                           # sus solicitudes técnicas vuelven a la bolsa
        mapa.update({f"mantenciones/{m['id']}/tecnico_id": "", f"mantenciones/{m['id']}/estado": "pendiente", f"mantenciones/{m['id']}/aceptada_en": None})
    rtdb().update(mapa)
    repo.invalidar("usuarios", "mantenciones")
    seguridad.revocar_sesiones_usuario(pk)
    seguridad.invalidar_cache_sesiones()
    _audit(request, "usuario_eliminado", "usuario", pk, {"rol": u.get("rol"), "solicitudes_devueltas": len(abiertas)})
    return sin_contenido()


@ruta(("PATCH", "DELETE"), roles=ADMIN)
def usuario_detalle(request, pk):
    if request.method == "DELETE":
        u = repo.get("usuarios", pk)
        if u is None or u.get("eliminado"):
            raise no_encontrado()
        return _eliminar_usuario(request, u, pk)
    d = validar(request.cuerpo, {"nombre": Campo(texto(2, 120)), "rol": Campo(ROLES), "activo": Campo(booleano),
                                 "password": Campo(texto(0, 128)), "instalaciones": Campo(lista_ids(200)), "dispositivos": Campo(lista_ids(500)),
                                 "suspension_motivo": Campo(texto(0, 300))}, parcial=True)
    u = repo.get("usuarios", pk)
    if u is None or u.get("eliminado"):
        raise no_encontrado()
    # Evita que el administrador se quite el acceso a sí mismo y deje el sistema sin administradores.
    if pk == request.usuario["id"] and (d.get("activo") is False or ("rol" in d and d["rol"] != "administrador")):
        raise conflicto("No puedes desactivar ni degradar tu propia cuenta.")
    if d.get("password"):
        _politica(d["password"], u["email"], d.get("nombre", u["nombre"]))
    if any(i not in repo.col("instalaciones") for i in d.get("instalaciones", [])):
        raise invalido("Una de las instalaciones indicadas no existe.")
    if "dispositivos" in d:
        _con_instalaciones_de([], d["dispositivos"])
    campos = {k: d[k] for k in ("nombre", "activo", "rol") if k in d}
    if d.get("activo") is False and u.get("activo", True):       # suspensión: queda quién, cuándo y por qué
        campos.update(suspension_motivo=d.get("suspension_motivo") or "", suspendido_en=ahora_ms(), suspendido_por=request.usuario["id"])
    elif d.get("activo") is True:
        campos.update(suspension_motivo=None, suspendido_en=None, suspendido_por=None)
    if d.get("password"):
        campos.update(hash=seguridad.hash_password(d["password"]), intentos_fallidos=0, bloqueado_hasta=None)
    if "instalaciones" in d or "dispositivos" in d:
        insts = _con_instalaciones_de(d["instalaciones"] if "instalaciones" in d else sorted((u.get("instalaciones") or {}).keys()), d.get("dispositivos", []))
        campos["instalaciones"] = {i: True for i in insts} or None
    if "dispositivos" in d:
        campos["dispositivos"] = {x: True for x in d["dispositivos"]} or None
    repo.actualizar("usuarios", pk, campos)
    if d.get("activo") is False or d.get("password") or "rol" in d:
        seguridad.revocar_sesiones_usuario(pk)            # efecto inmediato
    seguridad.invalidar_cache_sesiones()
    _audit(request, "usuario_modificado", "usuario", pk,
           {"campos": [k for k in d if k != "password"], "password_cambiada": bool(d.get("password"))})
    return sin_contenido()


# ============================================================ INSTALACIONES
def _esquema_instalacion(parcial=False):
    return {"codigo": Campo(texto(2, 40, r"[A-Za-z0-9_-]{2,40}", "Solo letras, números, guion y guion bajo (2–40)."), requerido=not parcial),
            "nombre": Campo(texto(2, 120), requerido=not parcial), "descripcion": Campo(texto(0, 500), requerido=False),
            "ubicacion": Campo(texto(0, 200), requerido=False), "activa": Campo(booleano, requerido=False),
            "tecnico_id": Campo(texto(0, 64), requerido=False)}


def _tecnico_de_instalacion(d):
    """«Conexión directa»: el técnico asignado recibe solo las solicitudes de esta instalación. Vacío = bolsa de todos los técnicos."""
    if d.get("tecnico_id"):
        u = repo.get("usuarios", d["tecnico_id"])
        if not u or u.get("rol") != "tecnico" or u.get("eliminado"):
            raise invalido("El técnico indicado no existe.")
    elif "tecnico_id" in d:
        d["tecnico_id"] = None


@ruta(("GET", "POST"))
def instalaciones(request):
    if request.method == "GET":
        vis = instalaciones_visibles(request.usuario)
        return JsonResponse([{"id": i["id"], "codigo": i["codigo"], "nombre": i["nombre"], "descripcion": i.get("descripcion"),
                              "ubicacion": i.get("ubicacion"), "tecnico_id": i.get("tecnico_id"),
                              "tecnico_nombre": repo.col("usuarios").get(i.get("tecnico_id"), {}).get("nombre"),
                              "activa": i.get("activa", True), "creada_en": iso(i.get("creada_en"))}
                             for i in sorted(repo.lista("instalaciones"), key=lambda x: x["nombre"].lower()) if _visible(i["id"], vis)], safe=False)
    _solo_admin(request)
    d = validar(request.cuerpo, _esquema_instalacion())
    _tecnico_de_instalacion(d)
    if any(i["codigo"] == d["codigo"] for i in repo.lista("instalaciones")):
        raise conflicto("Ya existe una instalación con ese código.")
    iid = repo.nuevo_id()
    repo.guardar("instalaciones", iid, {"codigo": d["codigo"], "nombre": d["nombre"], "descripcion": d.get("descripcion"),
                                        "ubicacion": d.get("ubicacion"), "tecnico_id": d.get("tecnico_id"),
                                        "activa": d.get("activa", True), "creada_en": ahora_ms()})
    _audit(request, "instalacion_creada", "instalacion", iid)
    return JsonResponse({"id": iid}, status=201)


@ruta(("PATCH",), roles=ADMIN)
def instalacion_detalle(request, pk):
    esquema = _esquema_instalacion(parcial=True)
    esquema.pop("codigo")
    d = validar(request.cuerpo, esquema, parcial=True)
    if pk not in repo.col("instalaciones"):
        raise no_encontrado()
    _tecnico_de_instalacion(d)
    repo.actualizar("instalaciones", pk, d)
    _audit(request, "instalacion_modificada", "instalacion", pk, d)
    return sin_contenido()


# ============================================================ CULTIVOS Y PLANTAS
@ruta(("GET", "POST"))
def cultivos(request):
    if request.method == "GET":
        vis, iid = instalaciones_visibles(request.usuario), request.GET.get("instalacion_id")
        return JsonResponse([{"id": c["id"], "instalacion_id": c["instalacion_id"], "nombre": c["nombre"],
                              "descripcion": c.get("descripcion"), "activo": c.get("activo", True)}
                             for c in sorted(repo.lista("cultivos"), key=lambda x: x["nombre"].lower())
                             if _visible(c["instalacion_id"], vis) and (not iid or c["instalacion_id"] == iid)], safe=False)
    _solo_admin(request)
    d = validar(request.cuerpo, {"instalacion_id": Campo(id_valor), "nombre": Campo(texto(2, 120)),
                                 "descripcion": Campo(texto(0, 500), requerido=False), "activo": Campo(booleano, requerido=False)})
    _existe("instalaciones", d["instalacion_id"])
    if any(c["instalacion_id"] == d["instalacion_id"] and c["nombre"] == d["nombre"] for c in repo.lista("cultivos")):
        raise conflicto("Ya existe un cultivo con ese nombre en la instalación.")
    cid = repo.nuevo_id()
    repo.guardar("cultivos", cid, {**d, "activo": d.get("activo", True)})
    _audit(request, "cultivo_creado", "cultivo", cid)
    return JsonResponse({"id": cid}, status=201)


@ruta(("PATCH", "DELETE"), roles=ADMIN)
def cultivo_detalle(request, pk):
    if pk not in repo.col("cultivos"):
        raise no_encontrado()
    if request.method == "DELETE":
        if any(x.get("cultivo_id") == pk for x in repo.lista("dispositivos")):
            raise conflicto("Este cultivo tiene un dispositivo asignado: elimina o reasigna el dispositivo primero.")
        mapa = {f"cultivos/{pk}": None}
        mapa.update({f"plantas/{p['id']}": None for p in repo.lista("plantas") if p["cultivo_id"] == pk})        # en cascada
        from core.rtdb import rtdb
        rtdb().update(mapa)
        repo.invalidar()
        _audit(request, "cultivo_eliminado", "cultivo", pk)
        return sin_contenido()
    d = validar(request.cuerpo, {"nombre": Campo(texto(2, 120)), "descripcion": Campo(texto(0, 500)), "activo": Campo(booleano)}, parcial=True)
    repo.actualizar("cultivos", pk, d)
    _audit(request, "cultivo_modificado", "cultivo", pk, d)
    return sin_contenido()


@ruta(("GET", "POST"))
def plantas(request):
    if request.method == "GET":
        vis, cid = instalaciones_visibles(request.usuario), request.GET.get("cultivo_id")
        cult = repo.col("cultivos")
        return JsonResponse([{"id": p["id"], "cultivo_id": p["cultivo_id"], "codigo": p["codigo"], "nombre": p["nombre"],
                              "activa": p.get("activa", True)}
                             for p in sorted(repo.lista("plantas"), key=lambda x: x["codigo"])
                             if p["cultivo_id"] in cult and _visible(cult[p["cultivo_id"]]["instalacion_id"], vis)
                             and (not cid or p["cultivo_id"] == cid)], safe=False)
    _solo_admin(request)
    d = validar(request.cuerpo, {"cultivo_id": Campo(id_valor), "codigo": Campo(texto(1, 40, r"[A-Za-z0-9_-]{1,40}")),
                                 "nombre": Campo(texto(1, 120)), "activa": Campo(booleano, requerido=False)})
    _existe("cultivos", d["cultivo_id"])
    if any(p["cultivo_id"] == d["cultivo_id"] and p["codigo"] == d["codigo"] for p in repo.lista("plantas")):
        raise conflicto("Ya existe una planta con ese código en el cultivo.")
    pid = repo.nuevo_id()
    repo.guardar("plantas", pid, {**d, "activa": d.get("activa", True)})
    _audit(request, "planta_creada", "planta", pid)
    return JsonResponse({"id": pid}, status=201)


@ruta(("PATCH", "DELETE"), roles=ADMIN)
def planta_detalle(request, pk):
    if pk not in repo.col("plantas"):
        raise no_encontrado()
    if request.method == "DELETE":
        repo.borrar("plantas", pk)
        _audit(request, "planta_eliminada", "planta", pk)
        return sin_contenido()
    d = validar(request.cuerpo, {"codigo": Campo(texto(1, 40, r"[A-Za-z0-9_-]{1,40}")), "nombre": Campo(texto(1, 120)),
                                 "activa": Campo(booleano)}, parcial=True)
    repo.actualizar("plantas", pk, d)
    _audit(request, "planta_modificada", "planta", pk, d)
    return sin_contenido()


# ============================================================ TIPOS DE SENSOR
def _tipo_publico(t):
    return {"id": t["id"], "codigo": t["codigo"], "nombre": t["nombre"], "tipo_variable": t["tipo_variable"], "unidad": t["unidad"],
            "valor_min": t.get("valor_min"), "valor_max": t.get("valor_max")}


@ruta(("GET", "POST"))
def tipos_sensor(request):
    if request.method == "GET":
        return JsonResponse([_tipo_publico(t) for t in sorted(repo.lista("tipos_sensor"), key=lambda x: x["nombre"].lower())], safe=False)
    _solo_admin(request)
    d = validar(request.cuerpo, {"codigo": Campo(texto(2, 40, _VARIABLE)), "nombre": Campo(texto(2, 80)),
                                 "tipo_variable": Campo(texto(2, 40, _VARIABLE)), "unidad": Campo(texto(1, 20)),
                                 "valor_min": Campo(numero, requerido=False, nulo=True), "valor_max": Campo(numero, requerido=False, nulo=True)})
    if d["codigo"] in repo.col("tipos_sensor"):
        raise conflicto("Ya existe un tipo de sensor con ese código.")
    repo.guardar("tipos_sensor", d["codigo"], {k: v for k, v in d.items() if v is not None})
    _audit(request, "tipo_sensor_creado", "tipo_sensor", d["codigo"])
    return JsonResponse({"id": d["codigo"]}, status=201)


@ruta(("PATCH",), roles=ADMIN)
def tipo_sensor_detalle(request, pk):
    d = validar(request.cuerpo, {"nombre": Campo(texto(2, 80)), "tipo_variable": Campo(texto(2, 40, _VARIABLE)),
                                 "unidad": Campo(texto(1, 20)), "valor_min": Campo(numero, nulo=True),
                                 "valor_max": Campo(numero, nulo=True)}, parcial=True)
    if pk not in repo.col("tipos_sensor"):
        raise no_encontrado()
    repo.actualizar("tipos_sensor", pk, d)
    _audit(request, "tipo_sensor_modificado", "tipo_sensor", pk, d)
    return sin_contenido()


# ============================================================ DISPOSITIVOS
def _aviso_secreto():
    return "Guarda este secreto ahora: no se volverá a mostrar."


@ruta(("GET", "POST"))
def dispositivos(request):
    if request.method == "GET":
        vis, iid = instalaciones_visibles(request.usuario), request.GET.get("instalacion_id")
        inst = repo.col("instalaciones")
        return JsonResponse([{
            "id": x["id"], "instalacion_id": x["instalacion_id"], "instalacion_codigo": inst.get(x["instalacion_id"], {}).get("codigo"),
            "cultivo_id": x.get("cultivo_id"), "cultivo_nombre": repo.col("cultivos").get(x.get("cultivo_id"), {}).get("nombre"),
            "codigo": x["codigo"], "nombre": x["nombre"], "activo": x.get("activo", True),
            "estado_conexion": x.get("estado_conexion", "sin_datos"), "ultima_comunicacion": iso(x.get("ultima_comunicacion")),
            "calidad_enlace": x.get("calidad_enlace"), "ubicacion": _ubic(x.get("ubicacion")),
            "en_venta": bool(x.get("clave_activacion_hash")) and not x.get("propietario_id"),
            "propietario": repo.col("usuarios").get(x.get("propietario_id"), {}).get("nombre") if x.get("propietario_id") else None,
            "responsables": [{"id": u["id"], "nombre": u["nombre"]} for u in repo.lista("usuarios")
                             if x["id"] in (u.get("dispositivos") or {}) and u.get("activo", True) and not u.get("eliminado")]}
            for x in sorted(repo.lista("dispositivos"), key=lambda x: x["codigo"])
            if _visible(x["instalacion_id"], vis) and (not iid or x["instalacion_id"] == iid)], safe=False)
    _solo_admin(request)
    d = validar(request.cuerpo, {"instalacion_id": Campo(id_valor), "cultivo_id": Campo(id_valor),
                                 "codigo": Campo(texto(2, 40, r"[A-Za-z0-9_-]{2,40}")), "nombre": Campo(texto(2, 120))})
    _existe("instalaciones", d["instalacion_id"])
    cultivo = repo.col("cultivos").get(d["cultivo_id"])
    if not cultivo or cultivo["instalacion_id"] != d["instalacion_id"]:
        raise invalido("El cultivo indicado no existe en esa instalación.")
    if any(x["codigo"] == d["codigo"] for x in repo.lista("dispositivos")):
        raise conflicto("Ya existe un dispositivo con ese código.")
    if any(x.get("cultivo_id") == d["cultivo_id"] for x in repo.lista("dispositivos")):     # regla del proyecto: un dispositivo por cultivo
        raise conflicto(f"El cultivo «{cultivo['nombre']}» ya tiene un dispositivo asignado (un dispositivo por cultivo).")
    secreto, nuevo = seguridad.token_aleatorio(32), repo.nuevo_id()
    repo.guardar("dispositivos", nuevo, {**d, "secreto_cif": seguridad.cifrar(secreto, f"dispositivo:{nuevo}"), "activo": True,
                                         "estado_conexion": "sin_datos", "creado_en": ahora_ms()})
    _audit(request, "dispositivo_creado", "dispositivo", nuevo)
    return JsonResponse({"id": nuevo, "secreto": secreto, "aviso": _aviso_secreto()}, status=201)


@ruta(("POST",), roles=ADMIN)
def dispositivo_clave_activacion(request, pk):
    """Genera la clave de activación de un equipo para venderlo: el comprador la usa (con el código del equipo) al crear su cuenta.
    La clave se muestra una sola vez; se guarda solo su huella. Un equipo ya entregado a un cliente no puede volver a activarse."""
    from core.vistas.clientes import generar_clave_activacion, hash_clave
    d = repo.get("dispositivos", pk)
    if d is None:
        raise no_encontrado()
    if d.get("propietario_id"):
        raise conflicto("Este equipo ya fue activado por un cliente.")
    clave = generar_clave_activacion()
    repo.actualizar("dispositivos", pk, {"clave_activacion_hash": hash_clave(clave)})
    _audit(request, "dispositivo_clave_activacion", "dispositivo", pk)
    return JsonResponse({"codigo": d["codigo"], "clave": clave, "aviso": "Entrégale al comprador el código del equipo y esta clave. No se volverá a mostrar."})


@ruta(("POST",), roles=ADMIN)
def dispositivo_rotar_secreto(request, pk):
    if pk not in repo.col("dispositivos"):
        raise no_encontrado()
    secreto = seguridad.token_aleatorio(32)
    repo.actualizar("dispositivos", pk, {"secreto_cif": seguridad.cifrar(secreto, f"dispositivo:{pk}")})
    _audit(request, "dispositivo_secreto_rotado", "dispositivo", pk)
    return JsonResponse({"secreto": secreto, "aviso": _aviso_secreto()})


@ruta(("PATCH",), roles=ADMIN)
def dispositivo_detalle(request, pk):
    d = validar(request.cuerpo, {"nombre": Campo(texto(2, 120)), "activo": Campo(booleano)}, parcial=True)
    if pk not in repo.col("dispositivos"):
        raise no_encontrado()
    repo.actualizar("dispositivos", pk, d)
    _audit(request, "dispositivo_modificado", "dispositivo", pk, d)
    return sin_contenido()


def _ubic(u):
    return {"lat": u["lat"], "lng": u["lng"], "precision": u.get("precision"), "origen": u.get("origen"), "actualizada_en": iso(u.get("actualizada_en"))} if u else None


# ============================================================ SENSORES
@ruta(("GET", "POST"))
def sensores(request):
    if request.method == "GET":
        vis, iid = instalaciones_visibles(request.usuario), request.GET.get("instalacion_id")
        disp, tipos = repo.col("dispositivos"), repo.col("tipos_sensor")
        salida = []
        for s in repo.lista("sensores"):
            dv, t = disp.get(s["dispositivo_id"]), tipos.get(s["tipo_sensor_id"])
            if not dv or not t or not _visible(dv["instalacion_id"], vis) or (iid and dv["instalacion_id"] != iid):
                continue
            salida.append({"id": s["id"], "dispositivo_id": s["dispositivo_id"], "dispositivo_codigo": dv["codigo"],
                           "instalacion_id": dv["instalacion_id"], "codigo": s["codigo"], "nombre": s["nombre"],
                           "activo": s.get("activo", True), "cultivo_id": dv.get("cultivo_id"),
                           "cultivo_nombre": repo.col("cultivos").get(dv.get("cultivo_id"), {}).get("nombre"),
                           "tipo_sensor_id": s["tipo_sensor_id"],
                           "tipo_nombre": t["nombre"], "tipo_variable": t["tipo_variable"], "unidad": t["unidad"]})
        return JsonResponse(sorted(salida, key=lambda x: (x["dispositivo_codigo"], x["codigo"])), safe=False)
    _solo_admin(request)
    # El cultivo de un sensor es el de su dispositivo (un dispositivo por cultivo): no se indica aparte.
    d = validar(request.cuerpo, {"dispositivo_id": Campo(id_valor), "tipo_sensor_id": Campo(id_valor),
                                 "codigo": Campo(texto(1, 40, r"[A-Za-z0-9_-]{1,40}")), "nombre": Campo(texto(1, 120))})
    _existe("dispositivos", d["dispositivo_id"])
    _existe("tipos_sensor", d["tipo_sensor_id"])
    if any(s["dispositivo_id"] == d["dispositivo_id"] and s["codigo"] == d["codigo"] for s in repo.lista("sensores")):
        raise conflicto("Ya existe un sensor con ese código en el dispositivo.")
    sid = repo.nuevo_id()
    repo.guardar("sensores", sid, {**{k: v for k, v in d.items() if v is not None}, "activo": True})
    _audit(request, "sensor_creado", "sensor", sid)
    return JsonResponse({"id": sid}, status=201)


@ruta(("PATCH",), roles=ADMIN)
def sensor_detalle(request, pk):
    d = validar(request.cuerpo, {"nombre": Campo(texto(1, 120)), "activo": Campo(booleano)}, parcial=True)
    s = repo.get("sensores", pk)
    if s is None:
        raise no_encontrado()
    repo.actualizar("sensores", pk, d)
    _audit(request, "sensor_modificado", "sensor", pk, d)
    return sin_contenido()


# ============================================================ REGLAS DE ALERTA
@ruta(("GET", "POST"))
def reglas(request):
    if request.method == "GET":
        tipos, sens = repo.col("tipos_sensor"), repo.col("sensores")
        return JsonResponse([{
            "id": r["id"], "nombre": r["nombre"], "tipo_sensor_id": r.get("tipo_sensor_id"),
            "tipo_nombre": tipos.get(r.get("tipo_sensor_id"), {}).get("nombre"), "sensor_id": r.get("sensor_id"),
            "sensor_codigo": sens.get(r.get("sensor_id"), {}).get("codigo"), "operador": r["operador"], "umbral": r["umbral"],
            "severidad": r.get("severidad") or "advertencia",
            "activa": r.get("activa", True)} for r in sorted(repo.lista("reglas"), key=lambda x: x["nombre"].lower())], safe=False)
    _solo_admin(request)
    d = validar(request.cuerpo, {"nombre": Campo(texto(2, 120)), "tipo_sensor_id": Campo(id_valor, requerido=False, nulo=True),
                                 "sensor_id": Campo(id_valor, requerido=False, nulo=True), "operador": Campo(_OPERADORES),
                                 "umbral": Campo(numero), "activa": Campo(booleano, requerido=False),
                                 "severidad": Campo(_SEVERIDAD, requerido=False)})
    if (d.get("tipo_sensor_id") is not None) == (d.get("sensor_id") is not None):
        raise invalido(detalles=[{"campo": "tipo_sensor_id", "mensaje": "Indica un tipo de sensor o un sensor (solo uno)."}])
    if d.get("tipo_sensor_id"):
        _existe("tipos_sensor", d["tipo_sensor_id"])
    if d.get("sensor_id"):
        _existe("sensores", d["sensor_id"])
    rid = repo.nuevo_id()
    repo.guardar("reglas", rid, {**{k: v for k, v in d.items() if v is not None}, "activa": d.get("activa", True)})
    _audit(request, "regla_creada", "regla_alerta", rid, {k: v for k, v in d.items() if v is not None})
    return JsonResponse({"id": rid}, status=201)


@ruta(("PATCH", "DELETE"), roles=ADMIN)
def regla_detalle(request, pk):
    if pk not in repo.col("reglas"):
        raise no_encontrado()
    if request.method == "DELETE":
        repo.borrar("reglas", pk)
        _audit(request, "regla_eliminada", "regla_alerta", pk)
        return sin_contenido()
    d = validar(request.cuerpo, {"nombre": Campo(texto(2, 120)), "operador": Campo(_OPERADORES), "umbral": Campo(numero),
                                 "activa": Campo(booleano), "severidad": Campo(_SEVERIDAD)}, parcial=True)
    repo.actualizar("reglas", pk, d)
    _audit(request, "regla_modificada", "regla_alerta", pk, d)
    return sin_contenido()


# ============================================================ PARÁMETROS DEL SISTEMA
CLAVES_PARAMETRO = ("minutos_sin_comunicacion", "retencion_historicos_dias", "google_maps_api_key")


@ruta(("GET",), roles=ADMIN)
def parametros(request):
    actuales = repo.col("parametros")
    return JsonResponse([{"clave": k, "valor": actuales.get(k, {}).get("valor", v[0]), "descripcion": v[1]}
                         for k, v in sorted(repo.PARAMETROS_INICIALES.items())], safe=False)


@ruta(("PUT",), roles=ADMIN)
def parametro_detalle(request, clave):
    if clave not in CLAVES_PARAMETRO:
        raise no_encontrado()
    if clave == "google_maps_api_key":
        d = validar(request.cuerpo, {"valor": Campo(texto(0, 120, r"[A-Za-z0-9_-]{0,120}", "La clave solo tiene letras, números, guion y guion bajo."))})
        repo.guardar("parametros", clave, {"valor": d["valor"]})
        _audit(request, "parametro_modificado", "parametro", clave, {"valor": "(clave de mapa actualizada)"})
        return sin_contenido()
    d = validar(request.cuerpo, {"valor": Campo(texto(1, 5, r"\d{1,5}", "Debe ser un entero positivo."))})
    if clave == "minutos_sin_comunicacion" and int(d["valor"]) < 1:
        raise invalido("Debe ser al menos 1.")
    repo.guardar("parametros", clave, {"valor": d["valor"]})
    _audit(request, "parametro_modificado", "parametro", clave, {"valor": d["valor"]})
    return sin_contenido()
