"""Vinculación de la cuenta con el equipo (celular o navegador) que se está usando.

Al iniciar sesión, la web y la app registran el equipo: queda guardado qué equipo usa cada persona, cuándo lo usó por última vez
y (en el celular) el token para recibir avisos push. La persona ve y desvincula sus equipos en «Mi cuenta»; el administrador ve
todos en la consola y puede revocar uno (se cierran sus sesiones y no puede volver a vincularse hasta que se reactive).

El equipo recibe un secreto aleatorio la primera vez: sin él nadie puede hacerse pasar por un equipo ya vinculado.
"""
import re

from django.http import JsonResponse

from core import repo, seguridad
from core.http import Campo, ErrorApi, ip_cliente, no_encontrado, ruta, sin_contenido, texto, validar
from core.push import hash_token
from core.rtdb import rtdb
from core.util import ahora_ms, iso

PLATAFORMAS = ("android", "web")


def _plataforma(x):
    if x not in PLATAFORMAS:
        raise ValueError("Plataforma no válida.")
    return x


def _publico(id_, e, actual=None):
    return {"id": id_, "nombre": e["nombre"], "plataforma": e["plataforma"], "creado_en": iso(e.get("creado_en")),
            "ultimo_uso": iso(e.get("ultimo_uso")), "ip": e.get("ip"), "revocado": bool(e.get("revocado")),
            "con_push": bool(e.get("con_push")), "actual": id_ == actual}


def _nombre_navegador(ua: str) -> str:
    ua = ua or ""
    sistema = next((n for p, n in ((r"Android", "Android"), (r"iPhone|iPad", "iOS"), (r"Windows", "Windows"), (r"Mac OS X", "macOS"), (r"Linux", "Linux")) if re.search(p, ua)), "equipo")
    nav = next((n for p, n in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"), (r"Chrome/", "Chrome"), (r"Safari/", "Safari")) if re.search(p, ua)), "Navegador")
    return f"{nav} en {sistema}"


def _dc_de_sesion(request):
    return (rtdb().get(f"sesiones/{seguridad.sha256_hex(request.token)}/dc") if request.token else None)


@ruta(("POST",))
def registrar(request):
    d = validar(request.cuerpo, {"id": Campo(texto(32, 32, r"[0-9a-f]{32}"), requerido=False), "secreto": Campo(texto(20, 100), requerido=False),
                                 "nombre": Campo(texto(1, 80), requerido=False), "plataforma": Campo(_plataforma),
                                 "push_token": Campo(texto(20, 400), requerido=False)})
    u, ahora, ua = request.usuario, ahora_ms(), request.META.get("HTTP_USER_AGENT", "")[:200]
    eid, e, secreto_nuevo = d.get("id"), None, None
    if eid and d.get("secreto"):
        e = rtdb().get(f"equipos/{eid}")
        if not e or e.get("usuario_id") != u["id"] or not seguridad.igual_seguro(seguridad.sha256_hex(d["secreto"]), e.get("secreto_hash", "")):
            e = None                       # no coincide: se trata como un equipo nuevo
    if e is not None and e.get("revocado"):
        seguridad.cerrar_sesion(request.token)
        raise ErrorApi(403, "equipo_revocado", "Este equipo fue desvinculado por un administrador. Pide que lo reactiven.")
    campos = {"ultimo_uso": ahora, "ip": ip_cliente(request) or "", "ua": ua}
    nombre = (d.get("nombre") or (_nombre_navegador(ua) if d["plataforma"] == "web" else "Celular Android")).strip()
    if e is None:
        eid, secreto_nuevo = repo.nuevo_id(), seguridad.token_aleatorio(24)       # id de 32 caracteres hexadecimales
        campos.update(usuario_id=u["id"], nombre=nombre, plataforma=d["plataforma"], creado_en=ahora, revocado=False,
                      secreto_hash=seguridad.sha256_hex(secreto_nuevo))
        rtdb().update({f"equipos/{eid}": campos, f"equipos_usuario/{u['id']}/{eid}": True})
        seguridad.registrar_auditoria(usuario_id=u["id"], accion="equipo_vinculado", entidad="equipo", entidad_id=eid, ip=ip_cliente(request),
                                      detalle={"nombre": nombre, "plataforma": d["plataforma"]})
    else:
        if d.get("nombre"):
            campos["nombre"] = nombre
        rtdb().update({f"equipos/{eid}/{k}": v for k, v in campos.items()})
    if d.get("push_token"):
        h = hash_token(d["push_token"])
        rtdb().update({f"push_tokens/{u['id']}/{h}": {"token": d["push_token"], "equipo_id": eid}, f"equipos/{eid}/con_push": True})
    if request.token:
        rtdb().set(f"sesiones/{seguridad.sha256_hex(request.token)}/dc", eid)
    resp = {"id": eid, "nombre": campos.get("nombre", nombre), "nuevo": secreto_nuevo is not None}
    if secreto_nuevo:
        resp["secreto"] = secreto_nuevo            # se entrega una sola vez
    return JsonResponse(resp, status=201 if secreto_nuevo else 200)


@ruta(("GET",))
def mis_equipos(request):
    uid, actual = request.usuario["id"], _dc_de_sesion(request)
    ids = (rtdb().get(f"equipos_usuario/{uid}", {}) or {}).keys()
    filas = [(i, rtdb().get(f"equipos/{i}")) for i in ids]
    return JsonResponse(sorted([_publico(i, e, actual) for i, e in filas if e], key=lambda x: x["ultimo_uso"] or "", reverse=True), safe=False)


def _quitar_equipo(eid, e, *, borrar):
    """Cierra las sesiones del equipo y retira sus avisos push. `borrar`: elimina el registro; si no, queda como revocado."""
    uid = e["usuario_id"]
    for h in list((rtdb().get(f"sesiones_usuario/{uid}", {}) or {}).keys()):
        if rtdb().get(f"sesiones/{h}/dc") == eid:
            seguridad._borrar_sesion(uid, h)
    cambios = {f"push_tokens/{uid}/{h}": None for h, t in (rtdb().get(f"push_tokens/{uid}", {}) or {}).items() if t.get("equipo_id") == eid}
    if borrar:
        cambios.update({f"equipos/{eid}": None, f"equipos_usuario/{uid}/{eid}": None})
    else:
        cambios.update({f"equipos/{eid}/revocado": True, f"equipos/{eid}/con_push": False})
    rtdb().update(cambios)


@ruta(("POST",))
def desvincular(request, pk):
    e = rtdb().get(f"equipos/{pk}")
    if not e or e.get("usuario_id") != request.usuario["id"]:
        raise no_encontrado()
    _quitar_equipo(pk, e, borrar=True)
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="equipo_desvinculado", entidad="equipo", entidad_id=pk, ip=ip_cliente(request))
    return sin_contenido()


# ------------------------------------------------------------------ administrador
@ruta(("GET",), roles=("administrador",))
def todos(request):
    usuarios = repo.col("usuarios")
    filas = []
    for i, e in (rtdb().get("equipos", {}) or {}).items():
        u = usuarios.get(e.get("usuario_id"), {})
        filas.append({**_publico(i, e), "usuario": u.get("nombre"), "email": u.get("email"), "rol": u.get("rol")})
    return JsonResponse(sorted(filas, key=lambda x: x["ultimo_uso"] or "", reverse=True), safe=False)


@ruta(("POST",), roles=("administrador",))
def revocar(request, pk):
    e = rtdb().get(f"equipos/{pk}")
    if not e:
        raise no_encontrado()
    _quitar_equipo(pk, e, borrar=False)
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="equipo_revocado", entidad="equipo", entidad_id=pk, ip=ip_cliente(request))
    return sin_contenido()


@ruta(("POST",), roles=("administrador",))
def reactivar(request, pk):
    e = rtdb().get(f"equipos/{pk}")
    if not e:
        raise no_encontrado()
    rtdb().update({f"equipos/{pk}/revocado": False})
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="equipo_reactivado", entidad="equipo", entidad_id=pk, ip=ip_cliente(request))
    return sin_contenido()
