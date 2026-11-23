"""Informes descargables y seguimiento de alertas (administrador)."""
from urllib.parse import quote

from django.http import HttpResponse, JsonResponse

from core import informes, repo, seguridad
from core.http import instalaciones_visibles, ip_cliente, ruta

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
    avisos = hojas["meta"].get("avisos") or []
    if avisos:                                                  # la pantalla lo muestra junto a la descarga
        r["X-Informe-Aviso"] = quote(" ".join(avisos))
        r["Access-Control-Expose-Headers"] = "X-Informe-Aviso, Content-Disposition"
    return r


@ruta(("GET",), roles=("administrador",))
def trabajadores(request):
    """Desempeño del personal en el período (para verlo en pantalla antes de descargar)."""
    f = informes.parametros(request.GET)
    personas, _, mant = informes.desempeno_trabajadores(f)
    return JsonResponse({"desde": f["desde_txt"], "hasta": f["hasta_txt"], "personas": personas, "mantenciones": len(mant),
                         "mantenciones_abiertas": sum(1 for m in mant if m.get("estado") in ("pendiente", "en_curso"))})


@ruta(("GET",), roles=("administrador", "supervisor"))
def seguimiento(request):
    return JsonResponse(informes.seguimiento(instalaciones_visibles(request.usuario)))
