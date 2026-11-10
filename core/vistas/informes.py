"""Informes descargables y seguimiento de alertas (administrador)."""
from django.http import HttpResponse, JsonResponse

from core import informes, repo, seguridad
from core.http import ip_cliente, ruta

TIPO_MIME = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv; charset=utf-8"}


@ruta(("GET",), roles=("administrador",))
def descargar(request):
    f = informes.parametros(request.GET)
    hojas = informes.construir(f, request.usuario)
    contenido = informes.a_xlsx(hojas, f) if f["formato"] == "xlsx" else informes.a_csv(hojas, f["tipo"])
    nombre = f"aquaplants-informe-{f['tipo']}-{f['desde_txt'].replace('-', '')}-{f['hasta_txt'].replace('-', '')}.{f['formato']}"
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="informe_generado", entidad="informe", ip=ip_cliente(request),
                                  detalle={k: f[k] for k in ("tipo", "formato", "desde_txt", "hasta_txt", "instalacion_id", "cultivo_id", "sensor_id", "agregacion")})
    r = HttpResponse(contenido, content_type=TIPO_MIME[f["formato"]])
    r["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return r


@ruta(("GET",), roles=("administrador", "supervisor"))
def seguimiento(request):
    return JsonResponse(informes.seguimiento())
