"""Páginas de error con su número (401, 403, 404, 500…) para quien entra desde el navegador. La API sigue respondiendo JSON."""
from html import escape

from django.http import HttpResponse

TEXTOS = {
    400: ("Solicitud no válida", "Lo que se envió no se pudo entender. Vuelve al inicio e inténtalo de nuevo."),
    401: ("Acceso no autorizado", "Debes iniciar sesión para ver esto. Si ya la iniciaste, tu sesión venció: ingresa de nuevo."),
    403: ("Acceso denegado", "Tu rol no tiene permiso para entrar aquí. Si crees que es un error, avisa al administrador."),
    404: ("No encontrado", "La dirección que buscas no existe o fue movida."),
    405: ("Método no permitido", "Esta dirección no admite esa operación."),
    413: ("Solicitud demasiado grande", "Lo que se envió supera el tamaño permitido."),
    429: ("Demasiados intentos", "Se hicieron muchas solicitudes seguidas. Espera unos minutos e inténtalo otra vez."),
    500: ("Error del servidor", "Algo falló de nuestro lado. Inténtalo otra vez en un momento."),
    503: ("Servicio no disponible", "El sistema no puede atender en este momento. Inténtalo de nuevo en unos minutos."),
}


def quiere_pagina(request):
    """True si quien pide es una persona con el navegador (no la interfaz, que usa fetch y espera JSON)."""
    return request.headers.get("Sec-Fetch-Mode") == "navigate" or (request.headers.get("Accept", "").startswith("text/html") and "fetch" not in request.headers.get("Sec-Fetch-Mode", ""))


def pagina_error(estado, mensaje=None):
    titulo, texto = TEXTOS.get(estado, ("Ocurrió un error", "Vuelve al inicio e inténtalo de nuevo."))
    acciones = '<a class="boton" href="/">Ir al inicio de sesión</a>' if estado == 401 else '<a class="boton" href="/">Volver al inicio</a>'
    cuerpo = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Error {estado} · AquaPlants</title><link rel="icon" type="image/png" href="/static/favicon.png"><link rel="stylesheet" href="/static/error.css"></head>
<body><main class="error-caja"><img class="error-logo" src="/static/logo-aquaplants.png" alt="AquaPlants">
<p class="error-num">{estado}</p><h1>{escape(titulo)}</h1><p class="error-texto">{escape(mensaje or texto)}</p>
<div class="error-botones">{acciones}</div><p class="error-pie">Error {estado} · AquaPlants</p></main></body></html>"""
    r = HttpResponse(cuerpo, status=estado, content_type="text/html; charset=utf-8")
    r["Cache-Control"] = "no-store"
    return r


def manejador(estado):
    return lambda request, exception=None: pagina_error(estado)


manejador_400, manejador_403, manejador_404, manejador_500 = manejador(400), manejador(403), manejador(404), manejador(500)
