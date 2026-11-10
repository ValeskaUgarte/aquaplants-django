"""Bandeja de notificaciones del usuario (campana en la web y en la app)."""
from django.http import JsonResponse

from core import notificaciones
from core.http import Campo, lista_ids, ruta, sin_contenido, validar
from core.util import iso


@ruta(("GET",))
def listar(request):
    uid = request.usuario["id"]
    items = notificaciones.listar(uid)
    return JsonResponse({
        "no_leidas": sum(1 for n in items if not n.get("leida")),
        "items": [{"id": n["id"], "tipo": n["tipo"], "titulo": n["titulo"], "mensaje": n["mensaje"], "alerta_id": n.get("alerta_id"),
                   "instalacion_id": n.get("instalacion_id"), "critica": bool(n.get("critica")), "creada_en": iso(n["creada_en"]),
                   "leida": bool(n.get("leida"))} for n in items]})


@ruta(("POST",))
def leer(request):
    d = validar(request.cuerpo, {"ids": Campo(lista_ids(100), requerido=False)})
    notificaciones.marcar_leidas(request.usuario["id"], set(d["ids"]) if d.get("ids") else None)
    return sin_contenido()
