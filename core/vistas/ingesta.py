"""Endpoint de ingesta para dispositivos y simuladores.  POST /api/v1/ingesta

Autenticación por firma HMAC (sin cookies). Límite grueso por IP antes de autenticar y límite fino por dispositivo después.
"""
from django.conf import settings
from django.http import JsonResponse

from core.http import ErrorApi, ip_cliente, permitir, ruta
from core.ingesta import autenticar_dispositivo, esquema_lote, procesar_lote
import json


def _excedido():
    return ErrorApi(429, "limite_excedido", "Límite de peticiones excedido.")


@ruta(("POST",), sesion=False, limite="ninguno", bruto=True)
def ingesta(request):
    if not permitir(f"ingesta-ip:{ip_cliente(request)}", settings.AQ["RATE_INGEST_POR_MIN"] * 4, 60):
        raise _excedido()
    try:
        cuerpo = json.loads(request.body.decode("utf-8"))
    except ValueError:
        raise ErrorApi(400, "json_invalido", "El cuerpo no es JSON válido.")
    disp, inst = autenticar_dispositivo(request)
    if not permitir(f"ingesta-dev:{disp['id']}", settings.AQ["RATE_INGEST_POR_MIN"], 60):
        raise _excedido()
    lote = esquema_lote(cuerpo)
    # El lote debe declarar la misma identidad con la que se autenticó (evita suplantación entre sistemas).
    if lote["id_dispositivo"] != disp["codigo"] or lote["id_sistema"] != inst["codigo"]:
        raise ErrorApi(403, "identidad_no_coincide", "id_sistema/id_dispositivo no corresponden al dispositivo autenticado.")
    return JsonResponse(procesar_lote(disp, lote), status=200)
