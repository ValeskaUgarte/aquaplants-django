"""Administración (solo administrador): usuarios, instalaciones, dispositivos, sensores y reglas de alerta."""
from django.http import JsonResponse

from core import repo, seguridad
from core.http import ErrorApi, PAT_ID, invalido, ip_cliente, numero, ruta, sin_contenido, texto
from core.rtdb import rtdb

ADMIN = ("administrador",)
ROLES = ("administrador", "supervisor", "encargado")


def _auditar(request, accion, entidad, detalle=""):
    seguridad.auditar(accion, request.usuario, entidad, detalle, ip_cliente(request))


# ------------------------------------------------------------------ usuarios
def _usuario_publico(u):
    return {"id": u["id"], "email": u["email"], "nombre": u["nombre"], "rol": u["rol"], "activo": u.get("activo", True),
            "instalaciones": sorted((u.get("instalaciones") or {}).keys())}


def _instalaciones_validas(request):
    ids = request.cuerpo.get("instalaciones", [])
    if not isinstance(ids, list) or any(i not in repo.coleccion("instalaciones") for i in ids):
        raise invalido("Alguna instalación indicada no existe.")
    return ids


@ruta(("GET", "POST"), roles=ADMIN)
def usuarios(request):
    if request.method == "GET":
        return JsonResponse({"usuarios": [_usuario_publico(u) for u in repo.usuarios()]})
    email = texto(request.cuerpo, "email", 5, 120, r"[^@\s]+@[^@\s]+\.[^@\s]+")
    nombre, rol = texto(request.cuerpo, "nombre", 2, 80), request.cuerpo.get("rol")
    password = texto(request.cuerpo, "password", 1, 200)
    if rol not in ROLES:
        raise invalido("Rol no válido.")
    if (error := seguridad.validar_politica(password)):
        raise invalido(error)
    if repo.usuario_por_email(email):
        raise ErrorApi(409, "ya_existe", "Ya existe un usuario con ese correo.")
    uid = repo.crear_usuario(email, nombre, rol, seguridad.hash_password(password), _instalaciones_validas(request))
    _auditar(request, "usuario_creado", f"usuario:{uid}", email)
    return JsonResponse(_usuario_publico(repo.usuario(uid)), status=201)


@ruta(("PATCH",), roles=ADMIN)
def usuario(request, uid):
    u = repo.usuario(uid)
    if not u:
        raise ErrorApi(404, "no_encontrado", "Usuario no encontrado.")
    c, campos = request.cuerpo, {}
    if "nombre" in c:
        campos["nombre"] = texto(c, "nombre", 2, 80)
    if "rol" in c:
        if c["rol"] not in ROLES:
            raise invalido("Rol no válido.")
        campos["rol"] = c["rol"]
    if "activo" in c:
        if not isinstance(c["activo"], bool):
            raise invalido("'activo' debe ser verdadero o falso.")
        if uid == request.usuario["id"] and not c["activo"]:
            raise ErrorApi(409, "no_permitido", "No puedes desactivar tu propia cuenta.")
        campos["activo"] = c["activo"]
    if "instalaciones" in c:
        campos["instalaciones"] = {i: True for i in _instalaciones_validas(request)} or None
    if "password" in c:
        if (error := seguridad.validar_politica(texto(c, "password", 1, 200))):
            raise invalido(error)
        campos["hash"] = seguridad.hash_password(c["password"])
        campos["fallos"], campos["bloqueado_hasta"] = 0, None
    repo.actualizar_campos("usuarios", uid, campos)
    seguridad.invalidar_cache_sesiones()
    _auditar(request, "usuario_modificado", f"usuario:{uid}", ",".join(k for k in campos if k != "hash"))
    return JsonResponse(_usuario_publico(repo.usuario(uid)))


# ------------------------------------------------------------------ instalaciones
@ruta(("GET", "POST"), roles=ADMIN)
def instalaciones(request):
    if request.method == "GET":
        return JsonResponse({"instalaciones": [{**v, "id": k} for k, v in repo.coleccion("instalaciones").items()]})
    codigo = texto(request.cuerpo, "codigo", 2, 40, PAT_ID)
    if codigo in repo.coleccion("instalaciones"):
        raise ErrorApi(409, "ya_existe", "Ya existe una instalación con ese código.")
    datos = {"nombre": texto(request.cuerpo, "nombre", 2, 80),
             "descripcion": texto(request.cuerpo, "descripcion", 0, 300, requerido=False) or "",
             "cultivo": {"nombre": texto(request.cuerpo, "cultivo", 1, 80, requerido=False) or "Cultivo"}}
    repo.guardar("instalaciones", codigo, datos)
    _auditar(request, "instalacion_creada", f"instalacion:{codigo}")
    return JsonResponse({**datos, "id": codigo}, status=201)


# ------------------------------------------------------------------ dispositivos y sensores
@ruta(("GET", "POST"), roles=ADMIN)
def dispositivos(request):
    if request.method == "GET":
        return JsonResponse({"dispositivos": [{**{k: v for k, v in d.items() if k != "secreto_cif"}, "codigo": k}
                                              for k, d in repo.coleccion("dispositivos").items()]})
    codigo = texto(request.cuerpo, "codigo", 2, 40, PAT_ID)
    inst = request.cuerpo.get("instalacion")
    if inst not in repo.coleccion("instalaciones"):
        raise invalido("La instalación indicada no existe.")
    if codigo in repo.coleccion("dispositivos"):
        raise ErrorApi(409, "ya_existe", "Ya existe un dispositivo con ese código.")
    secreto = seguridad.token_aleatorio(32)
    repo.guardar("dispositivos", codigo, {"instalacion": inst, "nombre": texto(request.cuerpo, "nombre", 2, 80),
                                          "secreto_cif": seguridad.cifrar(secreto, f"dispositivo:{codigo}"),
                                          "activo": True, "estado": "sin_datos"})
    _auditar(request, "dispositivo_creado", f"dispositivo:{codigo}")
    return JsonResponse({"codigo": codigo, "secreto": secreto,
                         "aviso": "Guarda este secreto ahora: no se vuelve a mostrar."}, status=201)


@ruta(("POST",), roles=ADMIN)
def dispositivo_secreto(request, codigo):
    if codigo not in repo.coleccion("dispositivos"):
        raise ErrorApi(404, "no_encontrado", "Dispositivo no encontrado.")
    secreto = seguridad.token_aleatorio(32)
    repo.actualizar_campos("dispositivos", codigo, {"secreto_cif": seguridad.cifrar(secreto, f"dispositivo:{codigo}")})
    _auditar(request, "dispositivo_secreto_rotado", f"dispositivo:{codigo}")
    return JsonResponse({"codigo": codigo, "secreto": secreto, "aviso": "Guarda este secreto ahora: no se vuelve a mostrar."})


@ruta(("POST",), roles=ADMIN)
def sensores(request):
    disp = request.cuerpo.get("dispositivo")
    d = repo.coleccion("dispositivos").get(disp)
    tipo = request.cuerpo.get("tipo")
    if not d:
        raise invalido("El dispositivo indicado no existe.")
    if tipo not in repo.coleccion("tipos"):
        raise invalido("Tipo de sensor no válido.")
    codigo = texto(request.cuerpo, "codigo", 1, 40, r"[A-Za-z0-9_-]+")
    clave = f"{d['instalacion']}_{codigo}"
    if clave in repo.coleccion("sensores"):
        raise ErrorApi(409, "ya_existe", "Ya existe un sensor con ese código en la instalación.")
    repo.guardar("sensores", clave, {"instalacion": d["instalacion"], "dispositivo": disp, "codigo": codigo, "tipo": tipo,
                                     "nombre": texto(request.cuerpo, "nombre", 2, 80),
                                     "unidad": repo.coleccion("tipos")[tipo]["unidad"], "activo": True})
    _auditar(request, "sensor_creado", f"sensor:{clave}")
    return JsonResponse({"id": clave}, status=201)


# ------------------------------------------------------------------ reglas de alerta
def _regla(c):
    if c.get("tipo") not in repo.coleccion("tipos"):
        raise invalido("Tipo de sensor no válido.")
    if c.get("operador") not in ("<", ">"):
        raise invalido("El operador debe ser < o >.")
    if c.get("severidad") not in ("advertencia", "critica"):
        raise invalido("La severidad debe ser advertencia o critica.")
    return {"nombre": texto(c, "nombre", 3, 80), "tipo": c["tipo"], "operador": c["operador"],
            "umbral": numero(c, "umbral"), "severidad": c["severidad"], "activa": c.get("activa", True) is not False}


@ruta(("POST",), roles=ADMIN)
def reglas(request):
    import secrets
    rid = "r_" + secrets.token_hex(4)
    repo.guardar("reglas", rid, _regla(request.cuerpo))
    _auditar(request, "regla_creada", f"regla:{rid}")
    return JsonResponse({"id": rid}, status=201)


@ruta(("PATCH", "DELETE"), roles=ADMIN)
def regla(request, rid):
    actual = repo.coleccion("reglas").get(rid)
    if not actual:
        raise ErrorApi(404, "no_encontrado", "Regla no encontrada.")
    if request.method == "DELETE":
        repo.borrar("reglas", rid)
        _auditar(request, "regla_eliminada", f"regla:{rid}")
        return sin_contenido()
    repo.guardar("reglas", rid, _regla({**actual, **request.cuerpo}))
    _auditar(request, "regla_modificada", f"regla:{rid}")
    return sin_contenido()
