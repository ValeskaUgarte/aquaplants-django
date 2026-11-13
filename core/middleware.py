"""Cabeceras HTTP de seguridad (equivalente a Helmet de la versión Node)."""
import uuid

from django.conf import settings

_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


_MAPAS = {"script-src": "https://maps.googleapis.com https://maps.gstatic.com", "style-src": "'unsafe-inline' https://fonts.googleapis.com",
          "img-src": "https://maps.googleapis.com https://maps.gstatic.com https://*.googleapis.com https://*.ggpht.com blob:",
          "connect-src": "https://maps.googleapis.com https://*.googleapis.com", "font-src": "https://fonts.gstatic.com"}


def _csp_con_mapas():
    """Solo si el administrador configuró la clave de Google Maps se permite cargar el mapa; el mapa incrustado simple solo abre google.com en un marco."""
    csp = _CSP + "; frame-src https://www.google.com; worker-src blob:"
    try:
        from core import repo
        if (repo.col("parametros").get("google_maps_api_key") or {}).get("valor"):
            for directiva, extra in _MAPAS.items():
                csp = csp.replace(f"{directiva} ", f"{directiva} {extra} ", 1)
    except Exception:  # noqa: BLE001
        pass
    return csp


class CabecerasSeguridad:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.id = uuid.uuid4().hex
        respuesta = self.get_response(request)
        csp = _csp_con_mapas() + ("; upgrade-insecure-requests" if settings.AQ["PRODUCCION"] else "")
        respuesta["Content-Security-Policy"] = csp
        respuesta["Referrer-Policy"] = "no-referrer"
        respuesta["X-Content-Type-Options"] = "nosniff"
        respuesta["X-Frame-Options"] = "DENY"
        respuesta["Cross-Origin-Opener-Policy"] = "same-origin"
        respuesta["Cross-Origin-Resource-Policy"] = "same-origin"
        respuesta["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        respuesta["X-Request-Id"] = request.id
        if settings.AQ["PRODUCCION"]:
            respuesta["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        if request.path.startswith("/api/"):
            respuesta["Cache-Control"] = "no-store"   # las respuestas de la API nunca se guardan en caché
        if "Server" in respuesta:
            del respuesta["Server"]
        return respuesta
