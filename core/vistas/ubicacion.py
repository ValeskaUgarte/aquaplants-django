"""Ubicación de los dispositivos. El GPS del propio dispositivo informa su posición en cada envío (ver ingesta.registrar_ubicacion);
aquí se consulta, se ve su recorrido y, si hace falta (equipo sin GPS o sin señal), un supervisor o el administrador la corrige a mano.
El dispositivo puede cambiar de lugar: siempre se muestra la última posición conocida, de cuándo es y de dónde viene (GPS o manual)."""
from django.http import JsonResponse

from core import cache_micro, repo, seguridad
from core.http import Campo, conflicto, instalaciones_visibles, invalido, ip_cliente, no_encontrado, numero, ruta, sin_contenido, texto, validar
from core.ingesta import registrar_ubicacion
from core.rtdb import rtdb
from core.util import ahora_ms, iso


def _puede_ver(usuario, disp):
    if usuario["rol"] == "tecnico":                       # el técnico solo ve los equipos de sus solicitudes (o de la bolsa)
        return any(m.get("dispositivo_id") == disp["id"] and m.get("estado") in ("pendiente", "en_curso", "completada")
                   and (m.get("tecnico_id") == usuario["id"] or not m.get("tecnico_id")) for m in repo.lista("mantenciones"))
    vis = instalaciones_visibles(usuario)
    return vis is None or disp["instalacion_id"] in vis


def _publica(d, inst, cult, usr):
    u = d.get("ubicacion")
    return {"id": d["id"], "codigo": d["codigo"], "nombre": d.get("nombre"), "instalacion": inst.get(d["instalacion_id"], {}).get("nombre"),
            "instalacion_id": d["instalacion_id"], "direccion": inst.get(d["instalacion_id"], {}).get("ubicacion"),
            "cultivo": cult.get(d.get("cultivo_id"), {}).get("nombre"), "estado_conexion": d.get("estado_conexion", "sin_datos"),
            "ultima_comunicacion": iso(d.get("ultima_comunicacion")),
            "ubicacion": ({"lat": u["lat"], "lng": u["lng"], "precision": u.get("precision"), "origen": u.get("origen"), "actualizada_en": iso(u.get("actualizada_en"))} if u else None),
            "responsables": [x["nombre"] for x in usr if d["id"] in (x.get("dispositivos") or {}) and x.get("activo", True) and not x.get("eliminado")]}


@ruta(("GET",))
def ubicaciones(request):
    inst, cult, usr = repo.col("instalaciones"), repo.col("cultivos"), repo.lista("usuarios")
    lista = [_publica(d, inst, cult, usr) for d in repo.lista("dispositivos") if _puede_ver(request.usuario, d)]
    return JsonResponse(sorted(lista, key=lambda x: (x["instalacion"] or "", x["codigo"])), safe=False)


@ruta(("GET",))
def trayecto(request, pk):
    d = repo.get("dispositivos", pk)
    if d is None or not _puede_ver(request.usuario, d):
        raise no_encontrado()
    try:
        limite = max(1, min(500, int(request.GET.get("limite", 100))))
    except ValueError:
        limite = 100
    puntos = [{"lat": v["lat"], "lng": v["lng"], "precision": v.get("p"), "origen": v.get("o"), "en": iso(int(k.split("_")[0]))}
              for k, v in rtdb().rango(f"ubicaciones/{pk}", ultimos=limite)]
    return JsonResponse({"dispositivo": d["codigo"], "puntos": puntos})


@ruta(("PATCH",), roles=("administrador", "supervisor"))
def ubicacion_manual(request, pk):
    """Para un equipo sin GPS, o cuando no hay señal: se fija a mano dónde está. En cuanto el GPS vuelve a informar, manda el GPS."""
    d = validar(request.cuerpo, {"lat": Campo(numero), "lng": Campo(numero)})
    if not (-90 <= d["lat"] <= 90 and -180 <= d["lng"] <= 180):
        raise invalido("Las coordenadas están fuera de rango (latitud −90 a 90, longitud −180 a 180).")
    disp = repo.get("dispositivos", pk)
    if disp is None or not _puede_ver(request.usuario, disp):
        raise no_encontrado()
    mapa = {}
    registrar_ubicacion(disp, {"lat": round(float(d["lat"]), 6), "lng": round(float(d["lng"]), 6)}, mapa, ahora_ms(), origen="manual")
    rtdb().update(mapa)
    repo.invalidar("dispositivos")
    cache_micro.limpiar()
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="ubicacion_dispositivo_modificada", entidad="dispositivo", entidad_id=pk,
                                  ip=ip_cliente(request), detalle={"lat": d["lat"], "lng": d["lng"]})
    return sin_contenido()


@ruta(("GET",))
def config_mapa(request):
    """Clave pública de Google Maps (se restringe por dominio en Google Cloud). Sin clave la pantalla usa el mapa incrustado simple."""
    return JsonResponse({"google_key": (repo.col("parametros").get("google_maps_api_key") or {}).get("valor") or ""})
