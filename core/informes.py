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
from core.vistas.mantencion import ESTADOS as ESTADOS_MANT, TIPOS as TIPOS_MANT
from core.http import ErrorApi
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms

ZONA = ZoneInfo("America/Santiago")
TOPE_LECTURA = 4_000_000      # mediciones que se leen de la base por informe (se procesan por tramos, sin guardarlas todas en memoria)
TOPE_FILAS = {"xlsx": 150_000, "csv": 800_000}   # filas de mediciones por archivo; si se superan, el informe se agrupa más en vez de fallar
AGREGACIONES = {"crudo": None, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
NIVELES = ("crudo", "5m", "15m", "1h", "1d")      # de más a menos detalle
NOMBRE_AGREGACION = {"crudo": "sin agrupar (cada medición)", "5m": "cada 5 minutos", "15m": "cada 15 minutos", "1h": "cada hora", "1d": "diaria"}
TIPOS = ("completo", "mediciones", "alertas", "eventos", "resumen", "trabajadores")
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
    if formato == "csv" and tipo == "completo":
        tipo = "mediciones"
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


class _Excede(Exception):
    """Las filas superan lo que cabe en el archivo con el detalle pedido: se repite con una agrupación mayor."""


def _procesar_sensor(sid, desde, hasta, bucket, usado, con_filas, cupo):
    """Lee las mediciones de un sensor POR TRAMOS y las va resumiendo sobre la marcha (no guarda todas en memoria).
    Devuelve (estadísticas, filas_sin_agrupar, grupos). `cupo` = filas que aún caben en el archivo."""
    est = {"n": 0, "min": math.inf, "max": -math.inf, "suma": 0.0, "ultimo": None, "ultima_t": None}
    crudas, grupos = [], {}
    cursor, fin = clave_ms(desde), clave_ms(hasta)
    while True:
        lote = rtdb().rango(f"mediciones/{sid}", desde=cursor, hasta=fin, primeros=5000)
        for k, v in lote:
            t = int(k)
            if t >= hasta:
                continue
            x = v["v"]
            est["n"] += 1; est["suma"] += x; est["ultimo"], est["ultima_t"] = x, t
            if x < est["min"]:
                est["min"] = x
            if x > est["max"]:
                est["max"] = x
            if not con_filas:
                continue
            if bucket is None:
                crudas.append((t, v))
            else:
                inicio = math.floor(t / 1000 / bucket) * bucket
                g = grupos.get(inicio)
                if g is None:
                    grupos[inicio] = [x, x, x, 1]                  # suma, mínimo, máximo, cantidad
                else:
                    g[0] += x; g[3] += 1
                    if x < g[1]:
                        g[1] = x
                    if x > g[2]:
                        g[2] = x
        usado[0] += len(lote)
        if usado[0] > TOPE_LECTURA:
            raise ErrorApi(413, "informe_demasiado_grande", "El rango tiene demasiadas mediciones para procesarlas de una sola vez. "
                           "Genera el informe por tramos (por ejemplo mes a mes) o filtra por instalación o cultivo.")
        if len(crudas) > cupo or len(grupos) > cupo:
            raise _Excede()
        if len(lote) < 5000:
            return est, crudas, grupos
        cursor = clave_ms(int(lote[-1][0]) + 1)


def _nivel_que_cabe(nivel, n_sensores, segundos, tope):
    """Primer nivel de agrupación (desde el pedido) cuyo máximo de filas posible cabe en el archivo. «Sin agrupar» no se puede acotar antes."""
    if nivel == 0:
        return 0
    while nivel < len(NIVELES) - 1 and n_sensores * math.ceil(segundos / AGREGACIONES[NIVELES[nivel]]) > tope:
        nivel += 1
    return nivel


def _medir(sensores, f, bucket, tope, con_filas):
    filas, resumen, usado = [], [], [0]
    for s in sensores:
        est, crudas, grupos = _procesar_sensor(s["id"], f["desde"], f["hasta"], bucket, usado, con_filas, tope - len(filas))
        n = est["n"]
        resumen.append([s["instalacion"], s["cultivo"], s["dispositivo"], s["variable"], s["unidad"], n, est["min"] if n else None,
                        est["suma"] / n if n else None, est["max"] if n else None, est["ultimo"], _local(est["ultima_t"]) if n else None])
        if bucket is None:
            filas += [[_local(t), s["instalacion"], s["cultivo"], s["dispositivo"], s["codigo"], s["variable"], v["v"], s["unidad"],
                       {"diferido": "Diferido"}.get(v.get("e"), "Conectado"), v.get("o", "dispositivo")] for t, v in crudas]
        else:
            filas += [[_local(k * 1000), s["instalacion"], s["cultivo"], s["dispositivo"], s["codigo"], s["variable"], g[0] / g[3], g[1], g[2], g[3], s["unidad"]]
                      for k, g in sorted(grupos.items())]
        if len(filas) > tope:
            raise _Excede()
    return filas, resumen


def construir(f, usuario):
    """Devuelve {'resumen': (cabeceras, filas), 'mediciones': ..., 'alertas': ..., 'eventos': ..., 'meta': {...}}.
    Un informe nunca falla por tamaño: si con el detalle pedido no cabe, se agrupa más (por hora, por día…) y se deja la nota en el archivo."""
    quiere = lambda *t: f["tipo"] in t  # noqa: E731
    hojas, meta = {}, {"filas_mediciones": 0, "avisos": []}
    if quiere("completo", "mediciones", "resumen"):
        sensores, tope = _sensores_filtrados(f), TOPE_FILAS[f["formato"]]
        con_filas, segundos = quiere("completo", "mediciones"), (f["hasta"] - f["desde"]) / 1000
        pedido = nivel = NIVELES.index(f["agregacion"])
        if not con_filas:
            nivel = len(NIVELES) - 1                       # solo se necesitan las estadísticas por sensor
        else:
            nivel = _nivel_que_cabe(nivel, len(sensores), segundos, tope)
        while True:
            try:
                filas, resumen_sensores = _medir(sensores, f, AGREGACIONES[NIVELES[nivel]], tope, con_filas)
                break
            except _Excede:                                # solo ocurre «sin agrupar» o estimaciones muy justas
                if nivel >= len(NIVELES) - 1:
                    raise ErrorApi(413, "informe_demasiado_grande", "Incluso agrupando por día el informe es demasiado grande para un solo archivo. "
                                   "Genéralo por instalación o cultivo, o por tramos de fechas.")
                nivel = _nivel_que_cabe(max(nivel + 1, 1), len(sensores), segundos, tope)
        if con_filas and nivel != pedido:
            meta["avisos"].append(f"Las mediciones se agruparon {NOMBRE_AGREGACION[NIVELES[nivel]]} (se pidió {NOMBRE_AGREGACION[NIVELES[pedido]]}) porque "
                                  f"con ese detalle el archivo superaba las {tope:,} filas permitidas. Para ver cada medición, acorta el rango o filtra por cultivo.".replace(",", "."))
        f = {**f, "agregacion": NIVELES[nivel]}
        bucket = AGREGACIONES[f["agregacion"]]
        if bucket is None:
            cab = ["Fecha y hora", "Instalación", "Cultivo", "Dispositivo", "Sensor", "Variable", "Valor", "Unidad", "Conexión", "Origen"]
        else:
            cab = ["Inicio del tramo", "Instalación", "Cultivo", "Dispositivo", "Sensor", "Variable", "Promedio", "Mínimo", "Máximo", "N.º mediciones", "Unidad"]
        if con_filas:
            hojas["mediciones"] = (cab, filas)
            meta["filas_mediciones"] = len(filas)
        hojas["_resumen_sensores"] = (["Instalación", "Cultivo", "Dispositivo", "Variable", "Unidad", "Mediciones", "Mínimo", "Promedio", "Máximo", "Último valor", "Última medición"],
                                      resumen_sensores)
    if quiere("completo", "alertas", "resumen"):
        hojas["alertas"] = (_cab_alertas(), _filas_alertas(f))
    if quiere("completo", "eventos"):
        hojas["eventos"] = _hoja_eventos(f)
    if quiere("completo", "trabajadores"):
        hojas.update(_hojas_trabajadores(f))
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
            "Resultado supervisor", "Comentario del supervisor", "Volvió a la normalidad", "Resuelta el", "Minutos abierta",
            "Solución no confirmada por la medición", "Mantención derivada", "Situación"]


def _situacion(a):
    e = a["estado"]
    if e == "resuelta":
        return "Resuelta automáticamente (el valor volvió a la normalidad)" if a.get("resuelta_auto") else "Resuelta"
    m = repo.col("mantenciones").get(a.get("mantencion_id"))
    if m and m.get("estado") in ("pendiente", "en_curso"):
        return f"PENDIENTE: derivada a mantención ({TIPOS_MANT.get(m['tipo'], m['tipo'])} · {ESTADOS_MANT.get(m['estado'])})"
    if a.get("solucion_cuestionada") and e == "reconocida":
        return "PENDIENTE: la solución informada no se reflejó en la medición" + ("; se recomienda derivar a un técnico" if a.get("requiere_tecnico") else "")
    if e == "revisada" and a.get("espera_medicion_hasta"):
        return "PENDIENTE: se informó la solución; falta que la medición la confirme"
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
            a.get("verificacion_nota") or None, _local(a.get("normalizada_en")), _local(a.get("resuelta_en")), round(abierta, 1),
            "Sí" if a.get("solucion_cuestionada") else "No", _texto_mantencion(a), _situacion(a)]


def _texto_mantencion(a):
    m = repo.col("mantenciones").get(a.get("mantencion_id"))
    return f"{TIPOS_MANT.get(m['tipo'], m['tipo'])} · {ESTADOS_MANT.get(m['estado'], m['estado'])}" if m else None


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


# ------------------------------------------------------------------ trabajadores
ROLES_TRABAJO = {"encargado_cultivo": "Encargado de cultivo", "supervisor": "Supervisor", "administrador": "Administrador"}
SOLUCIONES = ("dio solución", "resolvió")


def desempeno_trabajadores(f):
    """Qué hizo cada persona con las alertas del período y cuánto de eso resultó efectivo.
    Efectividad = soluciones informadas que se confirmaron (la alerta terminó resuelta) / (esas + las que la medición o el supervisor cuestionaron).
    Cumplimiento (solo encargados) = alertas de sus instalaciones que ellos reconocieron / alertas de sus instalaciones en el período."""
    alertas_ = alertas_en_rango({**f, "estado": "", "sensor_id": ""})
    inst, cult, disp = repo.col("instalaciones"), repo.col("cultivos"), repo.col("dispositivos")
    mant = [m for m in repo.lista("mantenciones") if f["desde"] <= m.get("creada_en", 0) < f["hasta"]
            and (not f["instalacion_id"] or m.get("instalacion_id") == f["instalacion_id"]) and (not f["cultivo_id"] or m.get("cultivo_id") == f["cultivo_id"])]
    salida, detalle = [], []
    for u in sorted(repo.lista("usuarios"), key=lambda x: (list(ROLES_TRABAJO).index(x.get("rol")) if x.get("rol") in ROLES_TRABAJO else 9, x.get("nombre", ""))):
        if not u.get("activo", True) or u.get("rol") not in ROLES_TRABAJO:
            continue
        enc = u["rol"] == "encargado_cultivo"
        ambito = [a for a in alertas_ if not enc or a.get("instalacion_id") in (u.get("instalaciones") or {})]
        r = dict(reconocidas=0, soluciones=0, efectivas=0, cuestionadas=0, pendientes=0, sin_solucion=0, confirmaciones=0, rechazos=0)
        resp, resol = [], []
        for a in alertas_:
            bit = sorted((a.get("bitacora") or {}).values(), key=lambda b: b.get("en", 0))
            mias = [b for b in bit if b.get("por") == u["id"]]
            if not mias:
                continue
            claims = [b for b in mias if b.get("accion") in SOLUCIONES]
            marcadas = set()
            for q in (a.get("cuestionadas") or {}).values():            # cada cuestionamiento recae sobre su última solución informada
                if q.get("a") == u["id"]:
                    previas = [i for i, b in enumerate(claims) if b.get("en", 0) <= q.get("en", 0)]
                    if previas:
                        marcadas.add(previas[-1])
            for i, b in enumerate(mias):
                acc = b.get("accion", "")
                resultado = ""
                if acc == "reconoció":
                    r["reconocidas"] += 1
                    resp.append(max(0, (b["en"] - a["creada_en"]) / 60000))
                elif acc in SOLUCIONES:
                    r["soluciones"] += 1
                    k = claims.index(b)
                    if k in marcadas:
                        r["cuestionadas"] += 1; resultado = "Cuestionada: la medición no la respaldó"
                    elif a["estado"] == "resuelta":
                        r["efectivas"] += 1; resultado = "Efectiva: la alerta quedó resuelta"
                        if a.get("resuelta_en"):
                            resol.append(max(0, (a["resuelta_en"] - a["creada_en"]) / 60000))
                    else:
                        r["pendientes"] += 1; resultado = "Sin resultado todavía"
                elif acc == "no pudo solucionar":
                    r["sin_solucion"] += 1
                elif acc.startswith("confirmó"):
                    r["confirmaciones"] += 1
                elif acc.startswith("rechazó"):
                    r["rechazos"] += 1
                detalle.append([_local(b["en"]), u["nombre"], ROLES_TRABAJO[u["rol"]], inst.get(a.get("instalacion_id"), {}).get("nombre"),
                                cult.get(a.get("cultivo_id") or disp.get(a.get("dispositivo_id"), {}).get("cultivo_id"), {}).get("nombre"), a.get("mensaje"), acc, b.get("nota") or None,
                                resultado or None])
        juzgadas = r["efectivas"] + r["cuestionadas"]
        mias_m = [m for m in mant if m.get("creada_por") == u["id"]]
        cerradas = [m for m in mant if m.get("completada_por") == u["id"] and m.get("estado") == "completada"]
        salida.append({"id": u["id"], "nombre": u["nombre"], "rol": u["rol"], "rol_texto": ROLES_TRABAJO[u["rol"]],
                       "alertas_ambito": len(ambito) if enc else None,
                       "cumplimiento": round(100 * r["reconocidas"] / len(ambito), 1) if enc and ambito else None,
                       "respuesta_min": round(sum(resp) / len(resp), 1) if resp else None,
                       "efectividad": round(100 * r["efectivas"] / juzgadas, 1) if juzgadas else None,
                       "resolucion_min": round(sum(resol) / len(resol), 1) if resol else None,
                       "derivaciones": len(mias_m), "mantenciones_cerradas": len(cerradas),
                       "sin_atender": sum(1 for a in ambito if not any(b.get("accion") == "reconoció" for b in (a.get("bitacora") or {}).values())) if enc else None, **r})
    return salida, detalle, mant


def _hojas_trabajadores(f):
    inst, cult, disp, usr = repo.col("instalaciones"), repo.col("cultivos"), repo.col("dispositivos"), repo.col("usuarios")
    personas, detalle, mant = desempeno_trabajadores(f)
    cab = ["Persona", "Rol", "Alertas de su ámbito", "Reconocidas por la persona", "Cumplimiento (%)", "Respuesta media (min)", "Soluciones informadas",
           "Efectivas", "Cuestionadas por la medición", "Sin resultado aún", "Efectividad (%)", "Resolución media (min)", "Revisiones sin solución",
           "Confirmaciones hechas", "Rechazos hechos", "Derivaciones a mantención", "Mantenciones cerradas", "Alertas sin atender"]
    filas = [[p["nombre"], p["rol_texto"], p["alertas_ambito"], p["reconocidas"], p["cumplimiento"], p["respuesta_min"], p["soluciones"], p["efectivas"], p["cuestionadas"],
              p["pendientes"], p["efectividad"], p["resolucion_min"], p["sin_solucion"], p["confirmaciones"], p["rechazos"], p["derivaciones"], p["mantenciones_cerradas"],
              p["sin_atender"]] for p in personas]
    cab_m = ["Derivada el", "Instalación", "Cultivo", "Dispositivo", "Tipo", "Estado", "Derivada por", "Técnico", "Motivo", "Realizada el", "Realizada por", "Resultado"]
    filas_m = [[_local(m.get("creada_en")), inst.get(m.get("instalacion_id"), {}).get("nombre"), cult.get(m.get("cultivo_id"), {}).get("nombre"),
                disp.get(m.get("dispositivo_id"), {}).get("codigo"), TIPOS_MANT.get(m["tipo"], m["tipo"]), ESTADOS_MANT.get(m["estado"], m["estado"]),
                usr.get(m.get("creada_por"), {}).get("nombre"), m.get("tecnico") or None, m.get("nota"), _local(m.get("completada_en")),
                usr.get(m.get("completada_por"), {}).get("nombre"), m.get("resultado")] for m in sorted(mant, key=lambda x: x.get("creada_en", 0))]
    return {"trabajadores": (cab, filas),
            "trabajadores_detalle": (["Fecha y hora", "Persona", "Rol", "Instalación", "Cultivo", "Alerta", "Acción", "Comentario", "Resultado"], sorted(detalle, key=lambda x: x[0])),
            "mantenciones": (cab_m, filas_m)}


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
    nombre = {"mediciones": "mediciones", "alertas": "alertas", "eventos": "eventos", "resumen": "_resumen_sensores", "trabajadores": "trabajadores"}[tipo]
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
    for aviso in meta.get("avisos", []):
        c = ws.cell(fila, 1, "Nota"); c.font = Font(bold=True, color="B26A00")
        ws.cell(fila, 2, aviso).font = Font(color="B26A00"); fila += 1
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

    titulos = {"mediciones": "Mediciones", "alertas": "Alertas", "eventos": "Eventos", "trabajadores": "Trabajadores",
               "trabajadores_detalle": "Detalle de gestión", "mantenciones": "Mantenciones"}
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
