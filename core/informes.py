"""Informes descargables (Excel y CSV) con el historial por fecha, instalación, cultivo y sensor, y el seguimiento de alertas.

Los lee desde Firebase con las mismas funciones que usa la web, así que el informe coincide con lo que se ve en pantalla.
Hojas del Excel: Resumen · Mediciones · Alertas · Eventos. Las horas salen en hora local (America/Santiago).
"""
import csv
import io
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

from core import alertas as modulo_alertas
from core import repo
from core.http import ErrorApi
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms

ZONA = ZoneInfo("America/Santiago")
TOPE_LECTURA = 400_000        # mediciones que se leen de la base por informe
TOPE_FILAS = 150_000          # filas que se escriben por hoja
AGREGACIONES = {"crudo": None, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
TIPOS = ("completo", "mediciones", "alertas", "eventos", "resumen")
ESTADOS = {"activa": "Activa", "reconocida": "Reconocida (en revisión)", "revisada": "Revisada (falta confirmar)", "resuelta": "Resuelta"}


def _local(ms):
    return datetime.fromtimestamp(ms / 1000, ZONA).replace(tzinfo=None) if ms else None


def _fecha(texto, fin=False):
    try:
        d = datetime.strptime(texto, "%Y-%m-%d").replace(tzinfo=ZONA)
    except (TypeError, ValueError):
        raise ErrorApi(400, "fecha_invalida", "Las fechas deben tener el formato AAAA-MM-DD.")
    if fin:
        d += timedelta(days=1)                       # «hasta» incluye el día completo
    return int(d.timestamp() * 1000)


def parametros(q):
    """Valida los filtros del informe. Devuelve un diccionario con el rango en ms y los filtros ya comprobados."""
    hoy = datetime.now(ZONA)
    desde_txt = q.get("desde") or (hoy - timedelta(days=7)).strftime("%Y-%m-%d")
    hasta_txt = q.get("hasta") or hoy.strftime("%Y-%m-%d")
    desde, hasta = _fecha(desde_txt), _fecha(hasta_txt, fin=True)
    if desde >= hasta:
        raise ErrorApi(400, "rango_invalido", "«Desde» debe ser anterior o igual a «hasta».")
    if hasta - desde > 366 * 86_400_000:
        raise ErrorApi(400, "rango_invalido", "El rango máximo de un informe es de 366 días.")
    tipo = q.get("tipo") or "completo"
    if tipo not in TIPOS:
        raise ErrorApi(400, "tipo_invalido", "Tipo de informe no válido.")
    formato = q.get("formato") or "xlsx"
    if formato not in ("xlsx", "csv"):
        raise ErrorApi(400, "formato_invalido", "El formato debe ser xlsx o csv.")
    if formato == "csv" and tipo in ("completo", "resumen"):
        tipo = "mediciones" if tipo == "completo" else "resumen"
    agregacion = q.get("agregacion") or "1h"
    if agregacion not in AGREGACIONES:
        raise ErrorApi(400, "agregacion_invalida", "Agregación no válida.")
    estado = q.get("estado") or ""
    if estado and estado not in ESTADOS and estado != "abiertas":
        raise ErrorApi(400, "estado_invalido", "Estado de alerta no válido.")
    f = {"desde": desde, "hasta": hasta, "desde_txt": desde_txt, "hasta_txt": hasta_txt, "tipo": tipo, "formato": formato, "agregacion": agregacion,
         "estado": estado, "instalacion_id": q.get("instalacion_id") or "", "cultivo_id": q.get("cultivo_id") or "",
         "sensor_id": q.get("sensor_id") or "", "tipo_variable": q.get("tipo_variable") or ""}
    if f["instalacion_id"] and f["instalacion_id"] not in repo.col("instalaciones"):
        raise ErrorApi(400, "instalacion_invalida", "La instalación indicada no existe.")
    if f["cultivo_id"] and f["cultivo_id"] not in repo.col("cultivos"):
        raise ErrorApi(400, "cultivo_invalido", "El cultivo indicado no existe.")
    return f


# ------------------------------------------------------------------ lectura
def _sensores_filtrados(f):
    disp, tipos, cult, inst = repo.col("dispositivos"), repo.col("tipos_sensor"), repo.col("cultivos"), repo.col("instalaciones")
    salida = []
    for s in repo.lista("sensores"):
        d, t = disp.get(s["dispositivo_id"]), tipos.get(s["tipo_sensor_id"])
        if not d or not t:
            continue
        if f["instalacion_id"] and d["instalacion_id"] != f["instalacion_id"]:
            continue
        if f["cultivo_id"] and d.get("cultivo_id") != f["cultivo_id"]:
            continue
        if f["sensor_id"] and s["id"] != f["sensor_id"]:
            continue
        if f["tipo_variable"] and t["tipo_variable"] != f["tipo_variable"]:
            continue
        salida.append({"id": s["id"], "codigo": s["codigo"], "variable": t["nombre"], "tipo_variable": t["tipo_variable"], "unidad": t["unidad"],
                       "dispositivo": d["codigo"], "instalacion": inst.get(d["instalacion_id"], {}).get("nombre", ""),
                       "cultivo": cult.get(d.get("cultivo_id"), {}).get("nombre", "Sin cultivo")})
    return sorted(salida, key=lambda x: (x["instalacion"].lower(), x["cultivo"].lower(), x["dispositivo"], x["codigo"]))


def _leer_mediciones(sid, desde, hasta, usado):
    """Lee por páginas todas las mediciones del sensor en el rango. `usado` = lista de 1 elemento con el total ya leído."""
    filas, cursor, fin = [], clave_ms(desde), clave_ms(hasta)
    while True:
        lote = rtdb().rango(f"mediciones/{sid}", desde=cursor, hasta=fin, primeros=5000)
        filas += [(int(k), v) for k, v in lote if int(k) < hasta]
        usado[0] += len(lote)
        if usado[0] > TOPE_LECTURA:
            raise ErrorApi(413, "informe_demasiado_grande", "El informe es demasiado grande. Reduce el rango de fechas o filtra por cultivo/sensor.")
        if len(lote) < 5000:
            return filas
        cursor = clave_ms(int(lote[-1][0]) + 1)


def construir(f, usuario):
    """Devuelve {'resumen': (cabeceras, filas), 'mediciones': ..., 'alertas': ..., 'eventos': ..., 'meta': {...}}."""
    quiere = lambda *t: f["tipo"] in t  # noqa: E731
    hojas, meta = {}, {"filas_mediciones": 0}
    resumen_sensores = []
    if quiere("completo", "mediciones", "resumen"):
        sensores, usado, bucket = _sensores_filtrados(f), [0], AGREGACIONES[f["agregacion"]]
        filas = []
        for s in sensores:
            datos = _leer_mediciones(s["id"], f["desde"], f["hasta"], usado)
            vals = [v["v"] for _, v in datos]
            resumen_sensores.append([s["instalacion"], s["cultivo"], s["dispositivo"], s["variable"], s["unidad"], len(vals),
                                     min(vals) if vals else None, sum(vals) / len(vals) if vals else None, max(vals) if vals else None,
                                     vals[-1] if vals else None, _local(datos[-1][0]) if datos else None])
            if not quiere("completo", "mediciones"):
                continue
            if bucket is None:
                filas += [[_local(t), s["instalacion"], s["cultivo"], s["dispositivo"], s["codigo"], s["variable"], v["v"], s["unidad"],
                           {"diferido": "Diferido"}.get(v.get("e"), "Conectado"), v.get("o", "dispositivo")] for t, v in datos]
            else:
                grupos = {}
                for t, v in datos:
                    k = math.floor(t / 1000 / bucket) * bucket
                    g = grupos.setdefault(k, [0.0, math.inf, -math.inf, 0])
                    g[0] += v["v"]; g[1] = min(g[1], v["v"]); g[2] = max(g[2], v["v"]); g[3] += 1
                filas += [[_local(k * 1000), s["instalacion"], s["cultivo"], s["dispositivo"], s["codigo"], s["variable"], g[0] / g[3], g[1], g[2], g[3], s["unidad"]]
                          for k, g in sorted(grupos.items())]
            if len(filas) > TOPE_FILAS:
                raise ErrorApi(413, "informe_demasiado_grande", "El informe supera las filas permitidas. Usa una agregación mayor, o reduce el rango o los filtros.")
        if bucket is None:
            cab = ["Fecha y hora", "Instalación", "Cultivo", "Dispositivo", "Sensor", "Variable", "Valor", "Unidad", "Conexión", "Origen"]
        else:
            cab = ["Inicio del tramo", "Instalación", "Cultivo", "Dispositivo", "Sensor", "Variable", "Promedio", "Mínimo", "Máximo", "N.º mediciones", "Unidad"]
        if quiere("completo", "mediciones"):
            hojas["mediciones"] = (cab, filas)
            meta["filas_mediciones"] = len(filas)
        hojas["_resumen_sensores"] = (["Instalación", "Cultivo", "Dispositivo", "Variable", "Unidad", "Mediciones", "Mínimo", "Promedio", "Máximo", "Último valor", "Última medición"],
                                      resumen_sensores)
    if quiere("completo", "alertas", "resumen"):
        hojas["alertas"] = (_cab_alertas(), _filas_alertas(f))
    if quiere("completo", "eventos"):
        hojas["eventos"] = _hoja_eventos(f)
    meta.update(generado=_local(ahora_ms()), usuario=usuario.get("nombre"), filtros=_descripcion_filtros(f))
    hojas["meta"] = meta
    return hojas


def _descripcion_filtros(f):
    inst, cult, tipos = repo.col("instalaciones"), repo.col("cultivos"), repo.col("tipos_sensor")
    sens = repo.get("sensores", f["sensor_id"]) if f["sensor_id"] else None
    return [("Período", f"{f['desde_txt']} al {f['hasta_txt']} (hora de Santiago)"),
            ("Instalación", inst.get(f["instalacion_id"], {}).get("nombre", "Todas") if f["instalacion_id"] else "Todas"),
            ("Cultivo", cult.get(f["cultivo_id"], {}).get("nombre", "Todos") if f["cultivo_id"] else "Todos"),
            ("Variable", next((t["nombre"] for t in tipos.values() if t["tipo_variable"] == f["tipo_variable"]), f["tipo_variable"]) if f["tipo_variable"] else "Todas"),
            ("Sensor", sens["codigo"] if sens else "Todos"),
            ("Agregación de mediciones", {"crudo": "Sin agregar (cada medición)", "5m": "Cada 5 minutos", "15m": "Cada 15 minutos", "1h": "Cada hora", "1d": "Diaria"}[f["agregacion"]]),
            ("Estado de alertas", ESTADOS.get(f["estado"], "Abiertas" if f["estado"] == "abiertas" else "Todos"))]


# ------------------------------------------------------------------ alertas (seguimiento)
def _cab_alertas():
    return ["Creada", "Estado", "Instalación", "Cultivo", "Dispositivo", "Sensor", "Tipo", "Detalle", "Valor detectado", "Reconocida por", "Reconocida el",
            "Revisada por", "Revisada el", "¿Quedó solucionado?", "Comentario del encargado", "Confirmada / rechazada por", "Fecha confirmación",
            "Resultado supervisor", "Comentario del supervisor", "Volvió a la normalidad", "Resuelta el", "Minutos abierta", "Situación"]


def _situacion(a):
    e = a["estado"]
    if e == "resuelta":
        return "Resuelta automáticamente (el valor volvió a la normalidad)" if a.get("resuelta_auto") else "Resuelta"
    if e == "activa":
        return "PENDIENTE: nadie ha ido a revisar"
    if e == "reconocida":
        return "PENDIENTE: el supervisor rechazó la revisión; el encargado debe volver a revisar" if a.get("verificacion_aprobada") is False \
            else "PENDIENTE: el encargado está revisando"
    return "PENDIENTE: falta la confirmación del supervisor"


def _fila_alerta(a, nombres):
    inst, sens, disp, usr, cult = nombres
    ahora = ahora_ms()
    abierta = ((a.get("resuelta_en") or ahora) - a["creada_en"]) / 60000
    si_no = lambda v: None if v is None else ("Sí" if v else "No")  # noqa: E731
    return [_local(a["creada_en"]), ESTADOS.get(a["estado"], a["estado"]), inst.get(a.get("instalacion_id"), {}).get("nombre"),
            cult.get(a.get("cultivo_id"), {}).get("nombre"), disp.get(a.get("dispositivo_id"), {}).get("codigo"), sens.get(a.get("sensor_id"), {}).get("codigo"),
            "Sin comunicación" if a.get("tipo") == "sin_comunicacion" else "Umbral", a.get("mensaje"), a.get("valor_detectado"),
            usr.get(a.get("reconocida_por"), {}).get("nombre"), _local(a.get("reconocida_en")),
            usr.get(a.get("revision_por"), {}).get("nombre"), _local(a.get("revision_en")), si_no(a.get("revision_solucionado")), a.get("revision_nota"),
            usr.get(a.get("verificacion_por"), {}).get("nombre"), _local(a.get("verificacion_en")),
            None if a.get("verificacion_aprobada") is None else ("Confirmó la solución" if a["verificacion_aprobada"] else "Rechazó la revisión"),
            a.get("verificacion_nota") or None, _local(a.get("normalizada_en")), _local(a.get("resuelta_en")), round(abierta, 1), _situacion(a)]


def _nombres():
    return (repo.col("instalaciones"), repo.col("sensores"), repo.col("dispositivos"), repo.col("usuarios"), repo.col("cultivos"))


def alertas_en_rango(f):
    """Alertas creadas en el período que cumplen los filtros (más antiguas primero)."""
    disp = repo.col("dispositivos")
    salida = []
    for k, a in rtdb().rango("alertas", desde=clave_ms(f["desde"]), hasta=clave_ms(f["hasta"])):
        if not (f["desde"] <= a.get("creada_en", 0) < f["hasta"]):
            continue
        d = disp.get(a.get("dispositivo_id"), {})
        if f["instalacion_id"] and a.get("instalacion_id") != f["instalacion_id"]:
            continue
        if f["cultivo_id"] and (a.get("cultivo_id") or d.get("cultivo_id")) != f["cultivo_id"]:
            continue
        if f["sensor_id"] and a.get("sensor_id") != f["sensor_id"]:
            continue
        if f["estado"] == "abiertas" and a["estado"] == "resuelta":
            continue
        if f["estado"] in ESTADOS and a["estado"] != f["estado"]:
            continue
        salida.append({**a, "id": k})
    return salida


def _filas_alertas(f):
    nombres = _nombres()
    return [_fila_alerta(a, nombres) for a in alertas_en_rango(f)]


def _hoja_eventos(f):
    inst, disp, tipos = repo.col("instalaciones"), repo.col("dispositivos"), {
        "dispositivo_sin_comunicacion": "Sin comunicación", "dispositivo_reconectado": "Reconexión",
        "sincronizacion_diferida": "Sincronización diferida", "medicion_rechazada": "Datos rechazados"}
    filas = []
    for k, e in rtdb().rango("eventos", desde=clave_ms(f["desde"]), hasta=clave_ms(f["hasta"])):
        if not (f["desde"] <= e.get("ocurrido_en", 0) < f["hasta"]):
            continue
        if f["instalacion_id"] and e.get("instalacion_id") != f["instalacion_id"]:
            continue
        d = disp.get(e.get("dispositivo_id"), {})
        if f["cultivo_id"] and d.get("cultivo_id") != f["cultivo_id"]:
            continue
        filas.append([_local(e["ocurrido_en"]), inst.get(e.get("instalacion_id"), {}).get("nombre"), repo.col("cultivos").get(d.get("cultivo_id"), {}).get("nombre"),
                      d.get("codigo"), tipos.get(e["tipo"], e["tipo"]), e.get("descripcion")])
    return (["Fecha y hora", "Instalación", "Cultivo", "Dispositivo", "Tipo de evento", "Descripción"], filas)


def seguimiento(usuario_visible=None):
    """Estado del flujo de las alertas para el administrador/supervisor: qué sigue pendiente, en qué paso, y las últimas resueltas."""
    nombres = _nombres()
    todas = modulo_alertas.ultimas(500)
    ahora = ahora_ms()
    cuenta = {"activa": 0, "reconocida": 0, "revisada": 0}
    pendientes, resueltas = [], []
    inst, sens, disp, usr, cult = nombres

    def ficha(a):
        return {"id": a["id"], "estado": a["estado"], "situacion": _situacion(a), "mensaje": a.get("mensaje"),
                "cultivo": cult.get(a.get("cultivo_id"), {}).get("nombre"), "dispositivo": disp.get(a.get("dispositivo_id"), {}).get("codigo"),
                "instalacion": inst.get(a.get("instalacion_id"), {}).get("nombre"), "creada_en": a["creada_en"], "minutos_abierta": round(((a.get("resuelta_en") or ahora) - a["creada_en"]) / 60000),
                "reconocida_por": usr.get(a.get("reconocida_por"), {}).get("nombre"),
                "revision_por": usr.get(a.get("revision_por"), {}).get("nombre"), "revision_solucionado": a.get("revision_solucionado"), "revision_nota": a.get("revision_nota"),
                "verificacion_por": usr.get(a.get("verificacion_por"), {}).get("nombre"), "verificacion_aprobada": a.get("verificacion_aprobada"),
                "verificacion_nota": a.get("verificacion_nota"), "resuelta_en": a.get("resuelta_en"), "normalizada_en": a.get("normalizada_en"),
                "resuelta_auto": bool(a.get("resuelta_auto"))}
    for a in todas:
        if a["estado"] == "resuelta":
            if len(resueltas) < 15:
                resueltas.append(ficha(a))
        else:
            cuenta[a["estado"]] = cuenta.get(a["estado"], 0) + 1
            pendientes.append(ficha(a))
    resueltas_24h = sum(1 for a in todas if a["estado"] == "resuelta" and (a.get("resuelta_en") or 0) > ahora - 86_400_000)
    return {"conteo": {**cuenta, "pendientes": len(pendientes), "resueltas_24h": resueltas_24h}, "pendientes": sorted(pendientes, key=lambda x: x["creada_en"]),
            "resueltas": resueltas}


# ------------------------------------------------------------------ archivos
def _fmt_csv(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    return v


def a_csv(hojas, tipo):
    nombre = {"mediciones": "mediciones", "alertas": "alertas", "eventos": "eventos", "resumen": "_resumen_sensores"}[tipo]
    cab, filas = hojas[nombre]
    sal = io.StringIO()
    w = csv.writer(sal, delimiter=";", lineterminator="\r\n")
    w.writerow(cab)
    for fila in filas:
        w.writerow([_fmt_csv(v) for v in fila])
    return ("\ufeff" + sal.getvalue()).encode("utf-8")      # BOM: Excel abre bien las tildes


def a_xlsx(hojas, f):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    verde, claro = "0B6E4F", "E3F3E8"
    cabecera = dict(font=Font(bold=True, color="FFFFFF"), fill=PatternFill("solid", fgColor=verde), alignment=Alignment(vertical="center", wrap_text=True))
    linea = Border(bottom=Side(style="thin", color="D9E1E6"))
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    ws.sheet_view.showGridLines = False
    meta = hojas["meta"]
    fila = 1
    try:                                                          # logo (si Pillow está instalado)
        from openpyxl.drawing.image import Image
        ruta = Path(settings.WEB_DIR) / "static" / "logo-aquaplants-fondo.png"
        if ruta.exists():
            img = Image(str(ruta)); img.height, img.width = 70, 60
            ws.add_image(img, "A1")
    except Exception:  # noqa: BLE001
        pass
    ws["C1"] = "AquaPlants · Informe de monitoreo"; ws["C1"].font = Font(bold=True, size=16, color=verde)
    ws["C2"] = f"Generado el {meta['generado']:%d-%m-%Y %H:%M} por {meta['usuario']}"; ws["C2"].font = Font(color="5B6B75")
    fila = 5
    ws.cell(fila, 1, "Filtros aplicados").font = Font(bold=True, size=12); fila += 1
    for k, v in meta["filtros"]:
        ws.cell(fila, 1, k).font = Font(bold=True); ws.cell(fila, 2, v); fila += 1
    fila += 1

    def tabla(ws, fila, titulo, cab, filas, anchos=None):
        if titulo:
            ws.cell(fila, 1, titulo).font = Font(bold=True, size=12); fila += 1
        for j, h in enumerate(cab, 1):
            c = ws.cell(fila, j, h)
            c.font, c.fill, c.alignment = cabecera["font"], cabecera["fill"], cabecera["alignment"]
        for i, datos in enumerate(filas, fila + 1):
            for j, v in enumerate(datos, 1):
                c = ws.cell(i, j, v); c.border = linea
                if isinstance(v, datetime):
                    c.number_format = "dd-mm-yyyy hh:mm:ss"
                elif isinstance(v, float):
                    c.number_format = "#,##0.000"
        return fila + len(filas) + 2

    if "_resumen_sensores" in hojas:
        cab, filas = hojas["_resumen_sensores"]
        fila = tabla(ws, fila, "Resumen por cultivo y variable", cab, filas)
    if "alertas" in hojas:
        al = hojas["alertas"][1]
        por_estado = {}
        for r in al:
            por_estado[r[1]] = por_estado.get(r[1], 0) + 1
        pend = sum(1 for r in al if str(r[-1]).startswith("PENDIENTE"))
        fila = tabla(ws, fila, "Alertas del período", ["Estado", "Cantidad"],
                     [[k, v] for k, v in sorted(por_estado.items())] + [["Total", len(al)], ["Pendientes de resolver", pend]])
    for j, ancho in enumerate([22, 24, 14, 26, 10, 12, 12, 12, 12, 14, 20], 1):
        ws.column_dimensions[get_column_letter(j)].width = ancho

    titulos = {"mediciones": "Mediciones", "alertas": "Alertas", "eventos": "Eventos"}
    for clave, titulo in titulos.items():
        if clave not in hojas:
            continue
        cab, filas = hojas[clave]
        h = wb.create_sheet(titulo)
        tabla(h, 1, None, cab, filas)
        h.freeze_panes = "A2"
        h.auto_filter.ref = f"A1:{get_column_letter(len(cab))}{max(len(filas) + 1, 2)}"
        for j, nombre in enumerate(cab, 1):
            muestra = [len(str(r[j - 1])) for r in filas[:300] if r[j - 1] is not None]
            h.column_dimensions[get_column_letter(j)].width = min(max([len(nombre) + 2] + muestra), 60 if clave == "alertas" else 40)
        if clave == "alertas":
            for r in h.iter_rows(min_row=2):
                if str(r[-1].value or "").startswith("PENDIENTE"):
                    r[-1].fill = PatternFill("solid", fgColor="FDEAEA")
                elif r[-1].value == "Resuelta":
                    r[-1].fill = PatternFill("solid", fgColor=claro)
    if f["tipo"] == "resumen" or len(wb.sheetnames) == 1:
        pass
    sal = io.BytesIO()
    wb.save(sal)
    return sal.getvalue()
