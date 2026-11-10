"""Infraestructura HTTP: errores JSON, validación de cuerpos, sesión + CSRF, roles y límites de ritmo."""
import functools
import hmac
import json
import logging
import math
import re
import threading
import time

from django.conf import settings
from django.http import HttpResponse, JsonResponse

from core import repo, seguridad
from core.rtdb import ErrorRTDB
from core.util import iso_a_ms

log = logging.getLogger("aquaplants.http")
MAX_CUERPO = 1_000_000
PAT_ID = r"[A-Za-z0-9_-]{1,64}"


class ErrorApi(Exception):
    def __init__(self, estado, codigo, mensaje, detalles=None):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje, self.detalles = estado, codigo, mensaje, detalles


def invalido(mensaje="Los datos enviados no son válidos.", detalles=None):
    return ErrorApi(400, "datos_invalidos", mensaje, detalles)


def no_encontrado():
    return ErrorApi(404, "no_encontrado", "No existe el recurso solicitado.")


def prohibido():
    return ErrorApi(403, "sin_permiso", "No tienes permiso para esta acción.")


def conflicto(mensaje):
    return ErrorApi(409, "conflicto", mensaje)


def sin_contenido():
    return HttpResponse(status=204)


def ip_cliente(request):
    if settings.AQ["TRUST_PROXY"] and request.META.get("HTTP_X_FORWARDED_FOR"):
        return request.META["HTTP_X_FORWARDED_FOR"].split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


# ------------------------------------------------------------------ límites de ritmo
_ritmo, _lock = {}, threading.Lock()


def permitir(clave, maximo, ventana_s):
    ahora = time.time()
    with _lock:
        v = [t for t in _ritmo.get(clave, []) if t > ahora - ventana_s]
        ok = len(v) < maximo
        if ok:
            v.append(ahora)
        _ritmo[clave] = v
        return ok


def reiniciar_limites():
    with _lock:
        _ritmo.clear()


# ------------------------------------------------------------------ validación (mismo esquema que el proyecto original)
class Campo:
    def __init__(self, validador, requerido=True, nulo=False):
        self.validador, self.requerido, self.nulo = validador, requerido, nulo


def texto(minimo, maximo, patron=None, mensaje=None):
    def v(x):
        if not isinstance(x, str) or not minimo <= len(x.strip()) <= maximo:
            raise ValueError(f"Debe ser texto de {minimo} a {maximo} caracteres.")
        x = x.strip()
        if patron and not re.fullmatch(patron, x):
            raise ValueError(mensaje or "Formato no permitido.")
        return x
    return v


def correo(x):
    if not isinstance(x, str) or len(x) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x.strip()):
        raise ValueError("Correo no válido.")
    return x.strip().lower()


def booleano(x):
    if not isinstance(x, bool):
        raise ValueError("Debe ser verdadero o falso.")
    return x


def numero(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("Debe ser un número.")
    return x


def entero(minimo=None, maximo=None):
    def v(x):
        if isinstance(x, bool) or not isinstance(x, int) or (minimo is not None and x < minimo) or (maximo is not None and x > maximo):
            raise ValueError("Debe ser un entero válido.")
        return x
    return v


def opcion(*valores):
    def v(x):
        if x not in valores:
            raise ValueError("Valor no permitido.")
        return x
    return v


def id_valor(x):
    if not isinstance(x, str) or not re.fullmatch(PAT_ID, x):
        raise ValueError("Identificador no válido.")
    return x


def lista_ids(maximo):
    def v(x):
        if not isinstance(x, list) or len(x) > maximo:
            raise ValueError("Debe ser una lista de identificadores.")
        return [id_valor(i) for i in x]
    return v


def fecha_iso(x):
    try:
        return iso_a_ms(x)
    except ValueError:
        raise ValueError("Fecha ISO 8601 no válida.")


def validar(datos, esquema, parcial=False):
    if not isinstance(datos, dict):
        raise invalido("El cuerpo debe ser un objeto JSON.")
    errores, salida = [], {}
    errores += [{"campo": k, "mensaje": "Campo no permitido."} for k in datos if k not in esquema]
    for nombre, c in esquema.items():
        if nombre not in datos:
            if c.requerido and not parcial:
                errores.append({"campo": nombre, "mensaje": "Es obligatorio."})
            continue
        if datos[nombre] is None:
            if c.nulo:
                salida[nombre] = None
            else:
                errores.append({"campo": nombre, "mensaje": "No puede ser nulo."})
            continue
        try:
            salida[nombre] = c.validador(datos[nombre])
        except ValueError as e:
            errores.append({"campo": nombre, "mensaje": str(e)})
    if errores:
        raise invalido(detalles=errores)
    if parcial and not salida:
        raise invalido("No hay cambios que aplicar.")
    return salida


# ------------------------------------------------------------------ alcance por instalación
def instalaciones_visibles(usuario):
    """None = todas (administrador y supervisor); conjunto de ids para el encargado de cultivo."""
    if usuario["rol"] in ("administrador", "supervisor"):
        return None
    return set((usuario.get("instalaciones") or {}).keys())


def exigir_acceso_instalacion(usuario, iid):
    vis = instalaciones_visibles(usuario)
    if iid not in repo.col("instalaciones") or (vis is not None and iid not in vis):
        raise no_encontrado()


# ------------------------------------------------------------------ decorador de vistas
def ruta(metodos, roles=None, sesion=True, limite="api", bruto=False):
    """sesion=False: pública o autenticada por otro medio (login, ingesta). limite: 'api' | 'login' | 'ninguno'."""
    def deco(vista):
        @functools.wraps(vista)
        def envoltura(request, **kw):
            try:
                if request.method not in metodos:
                    raise ErrorApi(405, "metodo_no_permitido", "Método no permitido.")
                if len(request.body) > MAX_CUERPO:
                    raise ErrorApi(413, "cuerpo_demasiado_grande", "El cuerpo supera el tamaño permitido.")
                ip = ip_cliente(request)
                if limite == "api" and not permitir(f"api:{ip}", settings.AQ["RATE_API_POR_MIN"], 60):
                    raise ErrorApi(429, "limite_excedido", "Demasiadas solicitudes. Intenta de nuevo en un momento.")
                if limite == "login" and not permitir(f"login:{ip}", settings.AQ["RATE_LOGIN_POR_15MIN"], 900):
                    raise ErrorApi(429, "limite_excedido", "Demasiados intentos. Espera unos minutos.")
                request.cuerpo = {}
                if not bruto and request.method in ("POST", "PUT", "PATCH", "DELETE") and request.body:
                    try:
                        request.cuerpo = json.loads(request.body.decode("utf-8"))
                    except ValueError:
                        raise invalido("El cuerpo no es JSON válido.")
                request.usuario = request.sesion = request.token = None
                if sesion:
                    token = request.COOKIES.get(settings.AQ["COOKIE_SESION"])
                    ses, usuario = seguridad.sesion_valida(token)
                    if not usuario:
                        raise ErrorApi(401, "no_autenticado", "Debes iniciar sesión.")
                    if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
                            request.headers.get("X-CSRF-Token", ""), ses["csrf"]):
                        raise ErrorApi(403, "csrf_invalido", "Token de seguridad inválido. Recarga la página.")
                    if roles and usuario["rol"] not in roles:
                        raise prohibido()
                    request.usuario, request.sesion, request.token = usuario, ses, token
                return vista(request, **kw)
            except ErrorApi as e:
                cuerpo = {"codigo": e.codigo, "mensaje": e.mensaje}
                if e.detalles:
                    cuerpo["detalles"] = e.detalles
                return JsonResponse({"error": cuerpo}, status=e.estado)
            except ErrorRTDB as e:
                log.error("Base de datos: %s", e)
                return JsonResponse({"error": {"codigo": "bd_no_disponible",
                                               "mensaje": "La base de datos no está disponible. Intenta de nuevo."}}, status=503)
            except Exception:  # noqa: BLE001
                log.exception("Error no controlado en %s", request.path)
                return JsonResponse({"error": {"codigo": "error_interno", "mensaje": "Error interno."}}, status=500)
        return envoltura
    return deco
