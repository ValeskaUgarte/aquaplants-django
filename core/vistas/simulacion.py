"""Control de la simulación desde la web y la app (solo Administrador)."""
from django.http import JsonResponse

from core import seguridad
from core.http import Campo, ErrorApi, ip_cliente, opcion, ruta, sin_contenido, validar
from core.rtdb import rtdb
from core.simulacion import escenarios, simulacion
from core.util import ahora_ms

ACCIONES = ("pausar", "reanudar", "anomalia", "corte", "escenario", "reparar",
            "alternar_anomalias", "alternar_datos_erroneos", "alternar_cortes")


@ruta(("GET",), roles=("administrador",))
def estado(request):
    return JsonResponse(simulacion.estado())


def _escenario(x):
    if x not in {e["codigo"] for e in escenarios()}:
        raise ValueError("Escenario no válido.")
    return x


@ruta(("POST",), roles=("administrador",))
def accion(request):
    d = validar(request.cuerpo, {"accion": Campo(opcion(*ACCIONES)), "dispositivo": Campo(lambda x: str(x)[:40], requerido=False),
                                 "escenario": Campo(_escenario, requerido=False)})
    if not simulacion.disponible:
        raise ErrorApi(409, "simulacion_no_disponible", "La simulación no está activa en este servidor.")
    if d["accion"] == "escenario" and not d.get("escenario"):
        raise ErrorApi(400, "datos_invalidos", "Indica qué falla quieres provocar.")
    if d["accion"] in ("escenario", "reparar") and not d.get("dispositivo"):
        raise ErrorApi(400, "datos_invalidos", "Indica el dispositivo (cultivo) en el que se provoca la falla.")
    simulacion.accion(d["accion"], d.get("dispositivo"), d.get("escenario"))
    rtdb().set("simulacion/estado", {"activa": simulacion.activa, "ultima_accion": d["accion"], "ts": ahora_ms()})
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion=f"simulacion_{d['accion']}", entidad="simulacion",
                                  ip=ip_cliente(request),
                                  detalle={k: d[k] for k in ("dispositivo", "escenario") if d.get(k)} or None)
    return sin_contenido()
