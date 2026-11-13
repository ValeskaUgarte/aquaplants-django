"""Derivación de alertas a mantención o técnico (visita, cambio de batería, cambio de dispositivo, etc.).

Cuando un corte de comunicación (o cualquier alerta) no se resuelve con la revisión del encargado, cualquiera de los tres roles puede
derivarla. La derivación crea una orden de mantención con su propio estado (pendiente → en curso → realizada o cancelada), queda en la
bitácora de la alerta, avisa a supervisores y administradores y alimenta el informe de trabajadores.
"""
from django.http import JsonResponse

from core import cache_micro, notificaciones, repo, seguridad
from core.http import (Campo, ErrorApi, conflicto, exigir_acceso_instalacion, instalaciones_visibles, invalido, ip_cliente, no_encontrado,
                       opcion, ruta, sin_contenido, texto, validar)
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms, iso

import secrets

TIPOS = {"tecnico": "Visita de técnico", "bateria": "Cambio de batería", "dispositivo": "Cambio de dispositivo",
         "sensor": "Revisión o cambio de sensor", "red": "Revisión de red, WiFi o energía", "otro": "Otra mantención"}
ESTADOS = {"pendiente": "Pendiente", "en_curso": "En curso", "completada": "Realizada", "cancelada": "Cancelada"}
ABIERTAS = ("pendiente", "en_curso")


def mantencion_publica(m, usuarios=None):
    if not m:
        return None
    usr = usuarios if usuarios is not None else repo.col("usuarios")
    return {"id": m.get("id"), "alerta_id": m.get("alerta_id"), "tipo": m["tipo"], "tipo_texto": TIPOS.get(m["tipo"], m["tipo"]),
            "estado": m["estado"], "estado_texto": ESTADOS.get(m["estado"], m["estado"]), "nota": m.get("nota"), "tecnico": m.get("tecnico") or None,
            "creada_en": iso(m.get("creada_en")), "creada_por": usr.get(m.get("creada_por"), {}).get("nombre"),
            "completada_en": iso(m.get("completada_en")), "completada_por": usr.get(m.get("completada_por"), {}).get("nombre"),
            "resultado": m.get("resultado")}


@ruta(("POST",), roles=("encargado_cultivo", "supervisor", "administrador"))
def derivar_alerta(request, pk):
    """Deriva una alerta abierta a un técnico o a una mantención."""
    d = validar(request.cuerpo, {"tipo": Campo(opcion(*TIPOS)), "nota": Campo(texto(5, 500)), "tecnico": Campo(texto(0, 120), requerido=False)})
    a = rtdb().get(f"alertas/{pk}")
    if a is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, a["instalacion_id"])
    if a["estado"] == "resuelta":
        raise conflicto("Esta alerta ya está resuelta: no necesita mantención.")
    previa = repo.get("mantenciones", a.get("mantencion_id")) if a.get("mantencion_id") else None
    if previa and previa["estado"] in ABIERTAS:
        raise conflicto("Esta alerta ya tiene una mantención en curso. Registra su resultado antes de derivarla otra vez.")
    ahora, mid = ahora_ms(), repo.nuevo_id()
    orden = {"alerta_id": pk, "instalacion_id": a["instalacion_id"], "dispositivo_id": a.get("dispositivo_id") or "", "cultivo_id": a.get("cultivo_id") or "",
             "tipo": d["tipo"], "nota": d["nota"], "tecnico": d.get("tecnico") or "", "estado": "pendiente", "creada_por": request.usuario["id"], "creada_en": ahora}
    mapa = {f"mantenciones/{mid}": orden, f"alertas/{pk}/mantencion_id": mid, f"alertas/{pk}/requiere_tecnico": False}
    quien = request.usuario
    mapa[f"alertas/{pk}/bitacora/{clave_ms(ahora)}_{secrets.token_hex(2)}"] = {
        "por": quien["id"], "rol": quien["rol"], "accion": "derivó a mantención", "en": ahora,
        "nota": f"{TIPOS[d['tipo']]}" + (f" ({d['tecnico']})" if d.get("tecnico") else "") + f": {d['nota']}"}
    try:
        notificaciones.por_derivacion(pk, a, quien, TIPOS[d["tipo"]], d["nota"], mapa)
        rtdb().update(mapa)
    except Exception:
        notificaciones.descartar_push()
        raise
    repo.invalidar("mantenciones")
    notificaciones.despachar_push()
    cache_micro.limpiar()
    seguridad.registrar_auditoria(usuario_id=quien["id"], accion="alerta_derivada", entidad="alerta", entidad_id=pk, ip=ip_cliente(request),
                                  detalle={"tipo": d["tipo"], "mantencion": mid})
    return JsonResponse({"id": mid}, status=201)


@ruta(("GET",))
def mantenciones(request):
    vis, iid, estado = instalaciones_visibles(request.usuario), request.GET.get("instalacion_id"), request.GET.get("estado")
    usr, inst, cult, disp = repo.col("usuarios"), repo.col("instalaciones"), repo.col("cultivos"), repo.col("dispositivos")
    salida = []
    for m in repo.lista("mantenciones"):
        if (vis is not None and m["instalacion_id"] not in vis) or (iid and m["instalacion_id"] != iid):
            continue
        if estado == "abiertas" and m["estado"] not in ABIERTAS or estado in ESTADOS and m["estado"] != estado:
            continue
        salida.append({**mantencion_publica(m, usr), "instalacion": inst.get(m["instalacion_id"], {}).get("nombre"),
                       "cultivo": cult.get(m.get("cultivo_id"), {}).get("nombre"), "dispositivo": disp.get(m.get("dispositivo_id"), {}).get("codigo")})
    return JsonResponse(sorted(salida, key=lambda x: x["creada_en"] or "", reverse=True), safe=False)


@ruta(("PATCH",), roles=("supervisor", "administrador"))
def mantencion_detalle(request, pk):
    """Supervisor o administrador actualizan la orden: la ponen en curso, la dan por realizada (con lo que se hizo) o la cancelan."""
    d = validar(request.cuerpo, {"estado": Campo(opcion("en_curso", "completada", "cancelada")), "nota": Campo(texto(0, 500), requerido=False),
                                 "tecnico": Campo(texto(0, 120), requerido=False)})
    m = repo.get("mantenciones", pk)
    if m is None:
        raise no_encontrado()
    exigir_acceso_instalacion(request.usuario, m["instalacion_id"])
    if m["estado"] not in ABIERTAS:
        raise conflicto("Esta mantención ya está cerrada.")
    if d["estado"] in ("completada", "cancelada") and len(d.get("nota", "")) < 5:
        raise invalido("Indica qué se hizo (o por qué se cancela).", [{"campo": "nota", "mensaje": "Es obligatoria al cerrar la mantención."}])
    ahora = ahora_ms()
    cambios = {"estado": d["estado"]}
    if d.get("tecnico"):
        cambios["tecnico"] = d["tecnico"]
    if d["estado"] != "en_curso":
        cambios.update(resultado=d["nota"], completada_en=ahora, completada_por=request.usuario["id"])
    mapa = {f"mantenciones/{pk}/{k}": v for k, v in cambios.items()}
    accion = {"en_curso": "puso la mantención en curso", "completada": "registró la mantención como realizada", "cancelada": "canceló la mantención"}[d["estado"]]
    mapa[f"alertas/{m['alerta_id']}/bitacora/{clave_ms(ahora)}_{secrets.token_hex(2)}"] = {
        "por": request.usuario["id"], "rol": request.usuario["rol"], "accion": accion, "nota": d.get("nota", ""), "en": ahora}
    rtdb().update(mapa)
    repo.invalidar("mantenciones")
    cache_micro.limpiar()
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="mantencion_actualizada", entidad="mantencion", entidad_id=pk,
                                  ip=ip_cliente(request), detalle={"estado": d["estado"]})
    return sin_contenido()
