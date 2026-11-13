"""Ingesta de mediciones: valida, persiste con timestamp, evalúa reglas y actualiza el estado.

Mismo comportamiento que el proyecto original. Cada medición se valida por separado: una mala no descarta el resto.
Todo el lote se escribe en Firebase con UNA escritura multi-ruta atómica.
"""
import re
import threading
import time
from datetime import datetime, timezone

from django.conf import settings

from core import alertas, cache_micro, notificaciones, repo, seguridad
from core.http import Campo, ErrorApi, entero, fecha_iso, numero, texto, validar
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms, distancia_m

PATRON_CODIGO = r"[A-Za-z0-9_-]{2,40}"
PATRON_SENSOR = r"[A-Za-z0-9_-]{1,40}"
PATRON_VARIABLE = r"[a-z0-9_]{2,40}"
OPERADORES = {">": lambda v, u: v > u, ">=": lambda v, u: v >= u, "<": lambda v, u: v < u, "<=": lambda v, u: v <= u}

_lock = threading.Lock()
_nonces = {}
_ultima = None            # sensor_id -> ts (ms) de la última medición registrada
_recientes = {}           # sensor_id -> {claves ts recientes}: detecta reenvíos sin consultar a Firebase


def _lista_mediciones(x):
    if not isinstance(x, list):
        raise ValueError("Debe ser una lista de mediciones.")
    if len(x) > settings.AQ["INGEST_MAX_BATCH"]:
        raise ValueError(f"Máximo {settings.AQ['INGEST_MAX_BATCH']} mediciones por lote.")
    return x


def _gps(x):
    """Posición que informa el GPS del dispositivo: {lat, lng, precision (m, opcional), altitud (m, opcional)}."""
    if not isinstance(x, dict) or set(x) - {"lat", "lng", "precision", "altitud", "timestamp"}:
        raise ValueError("gps inválido.")
    lat, lng = numero(x.get("lat")), numero(x.get("lng"))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Coordenadas fuera de rango.")
    sal = {"lat": round(float(lat), 6), "lng": round(float(lng), 6)}
    for k in ("precision", "altitud"):
        if x.get(k) is not None:
            sal[k] = round(float(numero(x[k])), 1)
    return sal


def registrar_ubicacion(disp, gps, mapa, ahora, origen="gps"):
    """Deja la posición actual del dispositivo y su historial (solo cuando se movió ≥ 30 m o pasaron 30 min, para no llenar la base).
    Si se movió ≥ 100 m queda un evento. El dispositivo puede cambiar de lugar: lo importante es saber dónde está."""
    prev = disp.get("ubicacion")
    mov = distancia_m(prev["lat"], prev["lng"], gps["lat"], gps["lng"]) if prev else None
    ubic = {**gps, "origen": origen, "actualizada_en": ahora, "guardada_en": (prev or {}).get("guardada_en", 0)}
    if prev is None or mov >= 30 or origen == "manual" or ahora - ubic["guardada_en"] >= 30 * 60_000:
        mapa[f"ubicaciones/{disp['id']}/{clave_ms(ahora)}"] = {"lat": gps["lat"], "lng": gps["lng"], "p": gps.get("precision"), "o": origen}
        ubic["guardada_en"] = ahora
    mapa[f"dispositivos/{disp['id']}/ubicacion"] = ubic
    if mov is not None and mov >= 100:
        alertas.evento(mapa, disp["instalacion_id"], disp["id"], "dispositivo_movido", f"El dispositivo se movió {round(mov)} m de su última posición.",
                       {"lat": gps["lat"], "lng": gps["lng"], "metros": round(mov)})


def esquema_lote(datos):
    return validar(datos, {
        "id_sistema": Campo(texto(2, 40, PATRON_CODIGO)), "id_dispositivo": Campo(texto(2, 40, PATRON_CODIGO)),
        "calidad_enlace": Campo(entero(0, 100), requerido=False), "gps": Campo(_gps, requerido=False),
        "mediciones": Campo(_lista_mediciones)})


def _medicion_valida(crudo):
    campos = {"id_sensor", "tipo_variable", "valor", "unidad", "timestamp", "estado_conexion"}
    if not isinstance(crudo, dict) or set(crudo) - campos:
        return None
    try:
        m = {"id_sensor": texto(1, 40, PATRON_SENSOR)(crudo.get("id_sensor")),
             "tipo_variable": texto(2, 40, PATRON_VARIABLE)(crudo.get("tipo_variable")),
             "valor": crudo.get("valor"), "unidad": texto(0, 20)(crudo.get("unidad")),
             "t": fecha_iso(crudo.get("timestamp")), "estado_conexion": crudo.get("estado_conexion", "conectado")}
    except ValueError:
        return None
    if isinstance(m["valor"], bool) or not isinstance(m["valor"], (int, float)):
        return None
    if m["estado_conexion"] not in ("conectado", "diferido"):
        return None
    m["valor"] = float(m["valor"])
    if m["valor"] != m["valor"] or abs(m["valor"]) >= 1e300:
        return None
    return m


def _num(x):
    return str(int(x)) if float(x).is_integer() else repr(float(x))


def reiniciar_estado():
    global _ultima
    with _lock:
        _nonces.clear(); _recientes.clear(); _ultima = None
    alertas.reiniciar()


def _cargar_ultima():
    global _ultima
    with _lock:
        if _ultima is None:
            _ultima = {k: v["ts"] for k, v in rtdb().get("ultima", {}).items()}
        return _ultima


# autenticación del dispositivo (firma HMAC)
def autenticar_dispositivo(request):
    """Verifica la firma HMAC. Defensas: ventana de tiempo, nonce de un solo uso, comparación en tiempo constante."""
    def fallo():
        return ErrorApi(401, "firma_invalida", "Autenticación de dispositivo inválida.")

    codigo, ts = request.META.get("HTTP_X_DEVICE_ID"), request.META.get("HTTP_X_TIMESTAMP")
    nonce, firma = request.META.get("HTTP_X_NONCE"), request.META.get("HTTP_X_SIGNATURE")
    if not all(isinstance(v, str) for v in (codigo, ts, nonce, firma)):
        raise fallo()
    if not (re.fullmatch(PATRON_CODIGO, codigo) and re.fullmatch(r"\d{10}", ts)
            and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce) and re.fullmatch(r"[0-9a-f]{64}", firma)):
        raise fallo()
    skew = settings.AQ["DEVICE_CLOCK_SKEW_SECONDS"]
    if abs(time.time() - int(ts)) > skew:
        raise fallo()
    disp = next((d for d in repo.lista("dispositivos") if d.get("codigo") == codigo), None)
    if disp is None:
        seguridad.gastar_tiempo_verificacion("x")
        raise fallo()
    try:
        secreto = seguridad.descifrar(disp["secreto_cif"], f"dispositivo:{disp['id']}")
    except ValueError:
        raise fallo()
    esperada = seguridad.firmar_dispositivo(secreto, ts, nonce, request.body.decode("utf-8", errors="replace"))
    if not seguridad.igual_seguro(esperada, firma):
        raise fallo()
    inst = repo.get("instalaciones", disp["instalacion_id"])
    if not disp.get("activo", True) or not inst or not inst.get("activa", True):
        raise ErrorApi(403, "dispositivo_inactivo", "Dispositivo o instalación desactivados.")
    ahora = time.time()
    with _lock:
        for k in [k for k, v in _nonces.items() if v < ahora]:
            del _nonces[k]
        if (disp["id"], nonce) in _nonces:
            raise ErrorApi(401, "nonce_repetido", "Petición repetida.")
        _nonces[(disp["id"], nonce)] = ahora + skew * 2
    return disp, inst


# procesamiento del lote
def procesar_lote(disp, lote, *, origen="api", actualizar_estado=True):
    ahora = ahora_ms()
    resultado = {"aceptadas": 0, "duplicadas": 0, "rechazadas": [], "alertas_generadas": []}
    limite_futuro = ahora + settings.AQ["DEVICE_CLOCK_SKEW_SECONDS"] * 1000
    iid = disp["instalacion_id"]
    sensores = {s["codigo"]: s for s in repo.lista("sensores") if s["dispositivo_id"] == disp["id"]}
    tipos = repo.col("tipos_sensor")
    cultivo = repo.col("cultivos").get(disp.get("cultivo_id"), {}).get("nombre", "")

    validas = []
    for indice, crudo in enumerate(lote["mediciones"]):
        m = _medicion_valida(crudo)
        motivo = None
        if m is None:
            motivo = "formato_invalido"
        else:
            s = sensores.get(m["id_sensor"])
            t = tipos.get(s["tipo_sensor_id"]) if s else None
            if s is None or not s.get("activo", True) or t is None:
                motivo = "sensor_desconocido_o_inactivo"
            elif m["tipo_variable"] != t["tipo_variable"]:
                motivo = "tipo_variable_no_coincide"
            elif crudo.get("unidad") != t["unidad"]:
                motivo = "unidad_no_coincide"
            elif (t.get("valor_min") is not None and m["valor"] < t["valor_min"]) or \
                 (t.get("valor_max") is not None and m["valor"] > t["valor_max"]):
                motivo = "valor_fuera_de_limites_de_validacion"
            elif m["t"] > limite_futuro:
                motivo = "timestamp_en_el_futuro"
        if motivo:
            resultado["rechazadas"].append({"indice": indice, "motivo": motivo})
        else:
            validas.append({**m, "sensor": s, "tipo": t})
    validas.sort(key=lambda v: v["t"])

    # Reenvíos: se detectan en memoria; a Firebase solo se pregunta (una vez por sensor) cuando el dato podría estar ya
    # guardado: carga manual, o un dato diferido no más nuevo que la última lectura conocida de ese sensor.
    # Así un dispositivo que vuelve de un corte sincroniza rápido aunque la base esté en la nube.
    ultimas = dict(_cargar_ultima())
    existentes = set()
    for sid in {v["sensor"]["id"] for v in validas}:
        mias = [v for v in validas if v["sensor"]["id"] == sid]
        tss = [clave_ms(v["t"]) for v in mias]
        existentes |= {(sid, k) for k in tss if k in _recientes.get(sid, ())}
        if origen != "api" or any(v["estado_conexion"] == "diferido" and v["t"] <= ultimas.get(sid, 0) for v in mias):
            existentes |= {(sid, k) for k, _ in rtdb().rango(f"mediciones/{sid}", desde=min(tss), hasta=max(tss))}

    mapa, contadores, nuevas_ultima, diferidas = {}, {}, {}, 0
    reglas = [r for r in repo.lista("reglas") if r.get("activa", True)]
    for m in validas:
        s, t = m["sensor"], m["tipo"]
        ident = (s["id"], clave_ms(m["t"]))
        if ident in existentes:
            resultado["duplicadas"] += 1
            continue
        existentes.add(ident)
        resultado["aceptadas"] += 1
        mapa[f"mediciones/{s['id']}/{ident[1]}"] = {
            "v": m["valor"], **({"e": "diferido"} if m["estado_conexion"] == "diferido" else {}),
            **({"o": origen} if origen != "api" else {})}
        hora = datetime.fromtimestamp(m["t"] / 1000, timezone.utc).strftime("%Y%m%d%H")
        contadores[hora] = contadores.get(hora, 0) + 1
        diferidas += m["estado_conexion"] == "diferido"
        if m["t"] < ultimas.get(s["id"], 0):
            continue                                  # dato fuera de orden: queda en el histórico, no altera alertas
        ultimas[s["id"]] = m["t"]
        nuevas_ultima[s["id"]] = {"ts": m["t"], "valor": m["valor"], "conexion": m["estado_conexion"]}
        for r in reglas:
            if not (r.get("sensor_id") == s["id"] or (not r.get("sensor_id") and r.get("tipo_sensor_id") == s["tipo_sensor_id"])):
                continue
            clave = f"u:{r['id']}:{s['id']}"
            if OPERADORES[r["operador"]](m["valor"], r["umbral"]):
                aid = alertas.abrir(clave, {
                    "tipo": "umbral", "problema": alertas.problema_de_regla(r["nombre"]), "instalacion_id": iid,
                    "severidad": r.get("severidad") or "advertencia", "dispositivo_id": disp["id"], "sensor_id": s["id"],
                    "regla_id": r["id"], "valor_detectado": m["valor"], "cultivo_id": disp.get("cultivo_id") or "",
                    "mensaje": f'{cultivo + " · " if cultivo else ""}{t["nombre"]} ({s["codigo"]}): {_num(m["valor"])} {t["unidad"]} incumple la regla '
                               f'"{r["nombre"]}" ({r["operador"]} {_num(r["umbral"])}).'}, mapa)
                if aid:
                    resultado["alertas_generadas"].append({"id_sensor": s["codigo"], "regla": r["nombre"], "valor": m["valor"]})
            else:
                alertas.normalizar(clave, mapa, f'{t["nombre"]} ({s["codigo"]}): {_num(m["valor"])} {t["unidad"]}, dentro de lo normal '
                                                f'(regla "{r["nombre"]}").')      # volvió a la normalidad: la alerta se resuelve sola

    for sid, u in nuevas_ultima.items():
        mapa[f"ultima/{sid}"] = u
    for hora, n in contadores.items():
        mapa[f"contadores/{iid}/{hora}"] = {".sv": {"increment": n}}
    if resultado["rechazadas"]:
        alertas.evento(mapa, iid, disp["id"], "medicion_rechazada",
                       f'{len(resultado["rechazadas"])} medición(es) rechazada(s) por validación', resultado["rechazadas"][:20])
    if diferidas:
        alertas.evento(mapa, iid, disp["id"], "sincronizacion_diferida",
                       f"Sincronización posterior a pérdida de conectividad: {diferidas} medición(es) diferida(s).")
    if actualizar_estado:
        mapa[f"dispositivos/{disp['id']}/estado_conexion"] = "en_linea"
        mapa[f"dispositivos/{disp['id']}/ultima_comunicacion"] = ahora
        if lote.get("calidad_enlace") is not None:
            mapa[f"dispositivos/{disp['id']}/calidad_enlace"] = lote["calidad_enlace"]
        if lote.get("gps"):
            registrar_ubicacion(disp, lote["gps"], mapa, ahora)
        if disp.get("estado_conexion") == "sin_comunicacion":
            alertas.normalizar(f"d:{disp['id']}", mapa, "El dispositivo volvió a comunicarse.")
            alertas.evento(mapa, iid, disp["id"], "dispositivo_reconectado", "El dispositivo volvió a comunicarse.")
    try:
        rtdb().update(mapa)
    except Exception:
        alertas.reiniciar()
        notificaciones.descartar_push()
        raise
    notificaciones.despachar_push()
    with _lock:
        _ultima.update({sid: u["ts"] for sid, u in nuevas_ultima.items()})
        for sid, k in existentes:
            r = _recientes.setdefault(sid, set())
            r.add(k)
            if len(r) > 5000:
                r.clear()
    repo.invalidar("dispositivos")
    cache_micro.limpiar()
    return resultado
