"""Estadísticas para ver de un vistazo cómo va la operación: alertas, dispositivos, mantenciones y cortes."""
from collections import Counter
from datetime import datetime

from django.http import JsonResponse

from core import alertas as modulo_alertas
from core import repo
from core.http import instalaciones_visibles, ruta
from core.informes import ZONA
from core.rtdb import rtdb
from core.util import ahora_ms
from core.vistas.mantencion import ACCIONES, RESOLUCIONES, TIPOS


def _prom(valores):
    return round(sum(valores) / len(valores), 1) if valores else None


def _mantenciones(lista, desde):
    m = [x for x in lista if x.get("creada_en", 0) >= desde]
    resp = [(x["aceptada_en"] - x["creada_en"]) / 60000 for x in m if x.get("aceptada_en")]
    resol = [(x["completada_en"] - x["creada_en"]) / 60000 for x in m if x.get("estado") == "completada" and x.get("completada_en")]
    hechas = [x for x in m if x.get("estado") == "completada"]
    return {"total": len(m), "abiertas": sum(1 for x in lista if x.get("estado") in ("pendiente", "en_curso")),
            "en_bolsa": sum(1 for x in lista if x.get("estado") == "pendiente" and not x.get("tecnico_id")),
            "completadas": len(hechas), "canceladas": sum(1 for x in m if x.get("estado") == "cancelada"),
            "respuesta_min": _prom(resp), "resolucion_min": _prom(resol),
            "por_resolucion": [{"nombre": RESOLUCIONES.get(k, k), "n": n} for k, n in Counter(x.get("resolucion") for x in hechas if x.get("resolucion")).most_common()],
            "por_tipo": [{"nombre": TIPOS.get(k, k), "n": n} for k, n in Counter(x["tipo"] for x in m).most_common()],
            "por_accion": [{"nombre": ACCIONES.get(k or "arreglo", k), "n": n} for k, n in Counter(x.get("accion") for x in m).most_common()]}


@ruta(("GET",))
def estadisticas(request):
    try:
        dias = max(7, min(180, int(request.GET.get("dias", 30))))
    except ValueError:
        dias = 30
    ahora = ahora_ms()
    desde = ahora - dias * 86400000
    uid, rol = request.usuario["id"], request.usuario["rol"]
    vis = instalaciones_visibles(request.usuario)
    if rol == "tecnico":                                           # el técnico solo ve lo suyo
        propias = [x for x in repo.lista("mantenciones") if x.get("tecnico_id") == uid]
        return JsonResponse({"dias": dias, "rol": rol, "mantenciones": _mantenciones(propias, desde)})
    visible = lambda iid: vis is None or iid in vis  # noqa: E731
    inst, cult = repo.col("instalaciones"), repo.col("cultivos")
    al = [a for a in modulo_alertas.ultimas(3000) if a.get("creada_en", 0) >= desde and visible(a.get("instalacion_id"))]
    dia = lambda ms: datetime.fromtimestamp(ms / 1000, ZONA).strftime("%Y-%m-%d")  # noqa: E731
    creadas, resueltas = Counter(dia(a["creada_en"]) for a in al), Counter(dia(a["resuelta_en"]) for a in al if a.get("resuelta_en"))
    dias_lista = [datetime.fromtimestamp((ahora - i * 86400000) / 1000, ZONA).strftime("%Y-%m-%d") for i in range(dias - 1, -1, -1)]
    res = [a for a in al if a.get("estado") == "resuelta" and a.get("resuelta_en")]
    reconocidas = [(b["en"] - a["creada_en"]) / 60000 for a in al for b in sorted((a.get("bitacora") or {}).values(), key=lambda x: x.get("en", 0))[:1] if b.get("accion") == "reconoció"]
    disp = [d for d in repo.lista("dispositivos") if visible(d["instalacion_id"]) and d.get("activo", True)]
    estados = Counter(d.get("estado_conexion", "sin_datos") for d in disp)
    eventos = [e for _, e in rtdb().rango("eventos", ultimos=4000) if e.get("ocurrido_en", 0) >= desde and visible(e.get("instalacion_id"))]
    cortes = Counter(e["dispositivo_id"] for e in eventos if e.get("tipo") == "sin_comunicacion")
    nombre_disp = {d["id"]: f"{d['codigo']} · {cult.get(d.get('cultivo_id'), {}).get('nombre', '')}".strip(" ·") for d in repo.lista("dispositivos")}
    por_cultivo = Counter(cult.get(a.get("cultivo_id"), {}).get("nombre") or "Sin cultivo" for a in al)
    mant = [x for x in repo.lista("mantenciones") if visible(x.get("instalacion_id"))]
    return JsonResponse({
        "dias": dias, "rol": rol,
        "kpis": {"alertas": len(al), "abiertas": sum(1 for a in al if a.get("estado") != "resuelta"), "resueltas": len(res),
                 "automaticas": sum(1 for a in res if a.get("resuelta_auto")), "cuestionadas": sum(1 for a in al if a.get("solucion_cuestionada")),
                 "resolucion_min": _prom([(a["resuelta_en"] - a["creada_en"]) / 60000 for a in res]), "respuesta_min": _prom(reconocidas),
                 "cortes": sum(cortes.values()), "movimientos": sum(1 for e in eventos if e.get("tipo") == "dispositivo_movido")},
        "serie": [{"dia": d, "alertas": creadas.get(d, 0), "resueltas": resueltas.get(d, 0)} for d in dias_lista],
        "por_tipo": [{"nombre": {"sin_comunicacion": "Sin comunicación", "umbral": "Fuera de rango"}.get(k, k), "n": n} for k, n in Counter(a.get("tipo") for a in al).most_common()],
        "por_severidad": [{"nombre": {"emergencia": "Emergencia", "advertencia": "Advertencia"}.get(k, k), "n": n} for k, n in Counter(a.get("severidad") for a in al).most_common()],
        "por_cultivo": [{"nombre": k, "n": n} for k, n in por_cultivo.most_common(8)],
        "cortes_por_dispositivo": [{"nombre": nombre_disp.get(k, k), "n": n} for k, n in cortes.most_common(8)],
        "dispositivos": {"total": len(disp), "en_linea": estados.get("en_linea", 0), "sin_comunicacion": estados.get("sin_comunicacion", 0),
                         "sin_datos": estados.get("sin_datos", 0), "con_gps": sum(1 for d in disp if d.get("ubicacion"))},
        "mantenciones": _mantenciones(mant, desde)})
