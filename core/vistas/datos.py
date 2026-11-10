"""Consulta de datos para la web y el móvil (todos los roles, acotada por alcance de instalaciones)."""
import time

from django.http import JsonResponse

from core import alertas, repo, seguridad
from core.http import ErrorApi, ruta, sin_contenido, ip_cliente, invalido
from core.rtdb import ErrorRTDB, rtdb
from core.util import ahora_ms, clave_ms

_micro = {}


def _fresco(clave, fn, ttl=1.5):
    """Caché de 1,5 s: muchos clientes consultando a la vez comparten una sola lectura a Firebase."""
    c = _micro.get(clave)
    if c and c[0] > time.time():
        return c[1]
    v = fn()
    _micro[clave] = (time.time() + ttl, v)
    return v


def _permitir(usuario, instalacion):
    if instalacion not in repo.instalaciones_de(usuario):
        raise ErrorApi(404, "no_encontrado", "No existe o no tienes acceso.")


@ruta(("GET",), publico=True)
def salud(request):
    try:
        rtdb().get("salud_ping")
        bd = "ok"
    except ErrorRTDB:
        bd = "error"
    return JsonResponse({"estado": "ok", "bd": bd, "modo_bd": rtdb().modo if bd == "ok" else None})


def _resumen_instalacion(iid, inst, sensores, estados, disps, abiertas):
    s_inst = [s for s in sensores.values() if s["instalacion"] == iid and s.get("activo", True)]
    est = estados.get(iid, {})
    lecturas = []
    for s in sorted(s_inst, key=lambda x: x["codigo"]):
        e = est.get(s["codigo"])
        lecturas.append({"sensor": f"{iid}_{s['codigo']}", "codigo": s["codigo"], "nombre": s["nombre"], "tipo": s["tipo"],
                         "unidad": s["unidad"], "valor": e["valor"] if e else None, "ts": e["ts"] if e else None,
                         "nivel": e["nivel"] if e else "sin_datos"})
    d_inst = [{"codigo": k, "nombre": d["nombre"], "estado": d.get("estado", "sin_datos"),
               "ultimo_contacto": d.get("ultimo_contacto"), "calidad_enlace": d.get("calidad_enlace")}
              for k, d in disps.items() if d["instalacion"] == iid]
    mias = [a for a in abiertas if a["instalacion"] == iid]
    vigentes = [a for a in mias if not a.get("normalizada_en")]      # las ya normalizadas no pintan la tarjeta de rojo
    nivel = "ok"
    if any(a["severidad"] == "critica" for a in vigentes) or any(d["estado"] == "sin_comunicacion" for d in d_inst):
        nivel = "critica"
    elif vigentes:
        nivel = "advertencia"
    elif not any(l["valor"] is not None for l in lecturas):
        nivel = "sin_datos"
    return {"id": iid, "nombre": inst["nombre"], "descripcion": inst.get("descripcion", ""),
            "cultivo": (inst.get("cultivo") or {}).get("nombre", ""), "nivel": nivel, "lecturas": lecturas,
            "dispositivos": d_inst, "alertas_abiertas": len(mias)}


@ruta(("GET",))
def resumen(request):
    ids = repo.instalaciones_de(request.usuario)
    insts, sensores = repo.coleccion("instalaciones"), repo.coleccion("sensores")
    estados = _fresco("estado", lambda: rtdb().get("estado", {}))
    disps = repo.coleccion("dispositivos")
    abiertas = [a for a in _fresco("alertas", lambda: alertas.ultimas(200))
                if a["estado"] != "resuelta" and a["instalacion"] in ids]
    lista = [_resumen_instalacion(i, insts[i], sensores, estados, disps, abiertas) for i in sorted(ids)]
    return JsonResponse({
        "ahora": ahora_ms(), "modo_bd": rtdb().modo,
        "instalaciones": lista,
        "totales": {"instalaciones": len(lista), "alertas_abiertas": len(abiertas),
                    "criticas": sum(1 for a in abiertas if a["severidad"] == "critica"),
                    "dispositivos_sin_comunicacion": sum(1 for i in lista for d in i["dispositivos"]
                                                         if d["estado"] == "sin_comunicacion"),
                    "dispositivos": sum(len(i["dispositivos"]) for i in lista)},
        "alertas": abiertas[:30]})


@ruta(("GET",))
def instalacion(request, iid):
    _permitir(request.usuario, iid)
    inst = repo.coleccion("instalaciones")[iid]
    sensores, disps = repo.coleccion("sensores"), repo.coleccion("dispositivos")
    estados = rtdb().get(f"estado/{iid}", {})
    abiertas = [a for a in alertas.ultimas(200) if a["estado"] != "resuelta" and a["instalacion"] == iid]
    r = _resumen_instalacion(iid, inst, sensores, {iid: estados}, disps, abiertas)
    r["plantas"] = [{"codigo": k, "nombre": p["nombre"]} for k, p in ((inst.get("cultivo") or {}).get("plantas") or {}).items()]
    return JsonResponse(r)


@ruta(("GET",))
def mediciones(request):
    sk = request.GET.get("sensor", "")
    s = repo.coleccion("sensores").get(sk)
    if not s:
        raise ErrorApi(404, "no_encontrado", "Sensor no encontrado.")
    _permitir(request.usuario, s["instalacion"])
    try:
        minutos = min(max(int(request.GET.get("minutos", "60")), 1), 1440)
    except ValueError:
        raise invalido("'minutos' debe ser un entero.")
    desde = clave_ms(ahora_ms() - minutos * 60_000)
    filas = rtdb().rango(f"mediciones/{sk}", desde=desde, ultimos=4000)
    puntos = [(int(k), v["v"]) for k, v in filas]
    n = len(puntos)
    if n > 240:                                             # promedios por tramo: gráficos livianos
        reducidos = []
        for i in range(240):
            ini, fin = int(i * n / 240), max(int(i * n / 240) + 1, int((i + 1) * n / 240))
            tramo = puntos[ini:fin]
            reducidos.append((tramo[len(tramo) // 2][0], sum(v for _, v in tramo) / len(tramo)))
        puntos = reducidos
    return JsonResponse({"sensor": sk, "unidad": s["unidad"], "nombre": s["nombre"], "puntos": [{"t": t, "v": v} for t, v in puntos]})


@ruta(("GET", "POST"))
def alertas_vista(request):
    if request.method == "POST":
        aid, accion = request.cuerpo.get("id"), request.cuerpo.get("accion")
        if accion not in ("reconocer", "resolver") or not isinstance(aid, str):
            raise invalido("Indica 'id' y 'accion' (reconocer o resolver).")
        if accion == "resolver" and request.usuario["rol"] == "encargado":
            raise ErrorApi(403, "sin_permiso", "No tienes permiso para esta acción.")
        a = rtdb().get(f"alertas/{aid}")
        if not a or a["instalacion"] not in repo.instalaciones_de(request.usuario):
            raise ErrorApi(404, "no_encontrado", "Alerta no encontrada.")
        (alertas.reconocer if accion == "reconocer" else alertas.resolver_manual)(aid, request.usuario)
        seguridad.auditar(f"alerta_{accion}", request.usuario, f"alerta:{aid}", ip=ip_cliente(request))
        _micro.pop("alertas", None)
        return sin_contenido()
    ids = repo.instalaciones_de(request.usuario)
    filtro = request.GET.get("estado", "abiertas")
    lista = [a for a in alertas.ultimas(200) if a["instalacion"] in ids and
             (filtro == "todas" or (filtro == "abiertas" and a["estado"] != "resuelta") or a["estado"] == filtro)]
    return JsonResponse({"alertas": lista[:100]})


@ruta(("GET",))
def tipos_reglas(request):
    return JsonResponse({"tipos": repo.coleccion("tipos"),
                         "reglas": [{**r, "id": k} for k, r in repo.coleccion("reglas").items()]})


@ruta(("GET",), roles=("administrador", "supervisor"))
def auditoria(request):
    filas = [v for _, v in rtdb().rango("auditoria", ultimos=100)]
    return JsonResponse({"registros": list(reversed(filas)), "cadena_integra": seguridad.cadena_integra(filas)})
