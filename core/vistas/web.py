"""Salud, interfaz web (monitoreo, escritorio) e interfaz móvil (operación en terreno)."""
from django.conf import settings
from django.http import FileResponse, HttpResponseRedirect, JsonResponse

from core.http import ruta
from core.rtdb import ErrorRTDB, rtdb


@ruta(("GET",), sesion=False, limite="ninguno")
def salud(request):
    """Chequeo de salud sin datos sensibles."""
    try:
        rtdb().get("salud_ping")
        return JsonResponse({"estado": "ok", "app": "aquaplants"})
    except ErrorRTDB:
        return JsonResponse({"estado": "degradado", "app": "aquaplants"}, status=503)


from core.paginas_error import pagina_error


def pagina_404(request, **kw):
    """Cualquier dirección que no existe, vista desde el navegador: página con su número (404)."""
    return pagina_error(404)


def no_encontrado_api(request, path=""):
    return JsonResponse({"error": {"codigo": "no_encontrado", "mensaje": "Ruta no encontrada."}}, status=404)


def _archivo(nombre):
    r = FileResponse(open(settings.WEB_DIR / nombre, "rb"), content_type="text/html; charset=utf-8")
    r["Cache-Control"] = "no-cache"
    return r


def inicio(request):
    # La app Android (WebView) se identifica con "; wv)" en el User-Agent: recibe la interfaz móvil automáticamente.
    if "; wv)" in request.headers.get("User-Agent", "") and request.GET.get("web") != "1":
        return HttpResponseRedirect("/movil")
    return _archivo("index.html")


def movil(request):
    return _archivo("movil.html")


def consola(request):
    """Consola de administración (equivalente al sitio /admin de Django): solo la usa el administrador."""
    return _archivo("admin.html")
