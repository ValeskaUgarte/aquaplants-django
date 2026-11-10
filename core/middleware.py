"""Cabeceras HTTP de seguridad (equivalente a Helmet de la versión Node)."""
import uuid

from django.conf import settings

_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


class CabecerasSeguridad:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.id = uuid.uuid4().hex
        respuesta = self.get_response(request)
        csp = _CSP + ("; upgrade-insecure-requests" if settings.AQ["PRODUCCION"] else "")
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
