"""Datos de demostración en Firebase RTDB. TODO es simulado (referencia pedagógica, no son datos reales de AquaPlants).

Regla del proyecto: UN DISPOSITIVO POR CULTIVO. Cada cultivo tiene su dispositivo AquaBrain, sus plantas, sus niveles
de operación y sus propias reglas de alerta (una lechuga y un tomate no necesitan el mismo caudal ni los mismos nutrientes).

VALORES REALES: las mediciones simuladas y los umbrales de cada cultivo salen de REFERENCIAS (más abajo), que copia los rangos de
las Tablas 12 a 15 del informe de la ES3 (lechuga, albahaca, tomate cherry y frutilla). Web, consola y app móvil solo muestran lo que
hay en la base, así que cambiar ese diccionario cambia las tres.

Solo siembra si la base está vacía. Las contraseñas se generan al azar (no hay credenciales por defecto) y se guardan en
data/credenciales-demo.txt; los secretos de los dispositivos virtuales, en data/dispositivos-demo.json.
"""
import json
import math
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core import acceso, repo, seguridad
from core.rtdb import rtdb
from core.simulacion import _modulo_simulador          # para usar la MISMA función de valores normales que el simulador
from core.util import ahora_ms, clave_ms

# código, nombre, unidad, límite mínimo, límite máximo de validación
TIPOS = [
    ("caudal", "Caudal", "L/min", 0, None),
    ("consumo_hidrico", "Consumo hídrico", "mL/planta/día", 0, None),
    ("nutrientes", "Conductividad eléctrica (EC)", "mS/cm", 0, None),     # antes «Nutrientes» en ppm: el informe separa la EC (sales totales) de N-P-K
    ("fotoperiodo", "Fotoperiodo", "h", 0, 24),
    ("parametro_cultivo", "Parámetro de cultivo", "u", None, None),
    # sensores ampliados: agua, luz, nutrientes por elemento, pH y gases contaminantes
    ("nivel_agua", "Nivel de agua", "%", 0, 100),
    ("luz_intensidad", "Intensidad de luz (PPFD)", "µmol/m²/s", 0, None),
    ("nitrogeno", "Nitrógeno (N)", "mg/L", 0, None),
    ("fosforo", "Fósforo (P)", "mg/L", 0, None),
    ("potasio", "Potasio (K)", "mg/L", 0, None),
    ("ph", "pH de la solución", "pH", 0, 14),
    ("co2", "CO₂ ambiental", "ppm", 0, None),
    ("amoniaco", "Amoníaco (NH₃)", "ppm", 0, None),
    ("temperatura", "Temperatura de la solución", "°C", -10, 60),
    # sensores del prototipo AquaBrain analizado en el ES1 (AHT10: temperatura y humedad del ambiente)
    ("humedad", "Humedad ambiental", "%", 0, 100),
    ("temperatura_ambiente", "Temperatura ambiente", "°C", -20, 60),
]
SENSORES = [("CAU1", "caudal"), ("CON1", "consumo_hidrico"), ("NUT1", "nutrientes"), ("FOT1", "fotoperiodo"), ("PAR1", "parametro_cultivo"),
            ("NIV1", "nivel_agua"), ("LUZ1", "luz_intensidad"), ("NIT1", "nitrogeno"), ("FOS1", "fosforo"), ("POT1", "potasio"),
            ("PH1", "ph"), ("CO21", "co2"), ("NH31", "amoniaco"), ("TEM1", "temperatura"),
            ("HUM1", "humedad"), ("TAM1", "temperatura_ambiente")]
# Ubicación de cada instalación (RF-01: nombre y ubicación).
UBICACIONES = {"INV-NORTE": "Sector norte, invernadero 1 (simulado)", "INV-SUR": "Sector sur, invernadero 2 (simulado)"}

# Instalación -> cultivos. «niveles» solo conserva lo que el informe NO trae (caudal L/min y parámetro de cultivo): el resto de las
# variables (pH, EC, temperaturas, humedad, fotoperiodo, PPFD, N-P-K, consumo) sale de REFERENCIAS.
INSTALACIONES = [
    ("INV-NORTE", "Invernadero Norte", "Módulo de hojas verdes y aromáticas", [
        {"nombre": "Lechuga", "descripcion": "Lactuca sativa (variedad costina)", "dispositivo": "AQB-01",
         "plantas": ["Lechuga costina 1", "Lechuga costina 2", "Lechuga costina 3", "Lechuga costina 4"],
         "niveles": {"caudal": 2.0, "parametro_cultivo": 6.0}},
        {"nombre": "Albahaca", "descripcion": "Ocimum basilicum", "dispositivo": "AQB-02",
         "plantas": ["Albahaca genovesa 1", "Albahaca genovesa 2", "Albahaca genovesa 3"],
         "niveles": {"caudal": 2.4, "parametro_cultivo": 6.2}},
    ]),
    ("INV-SUR", "Invernadero Sur", "Módulo de cultivos de fruto", [
        {"nombre": "Tomate cherry", "descripcion": "Solanum lycopersicum var. cerasiforme", "dispositivo": "AQB-03",
         "plantas": ["Tomate cherry 1", "Tomate cherry 2", "Tomate cherry 3"],
         "niveles": {"caudal": 3.2, "parametro_cultivo": 6.0}},
        {"nombre": "Frutilla", "descripcion": "Fragaria × ananassa", "dispositivo": "AQB-04",
         "plantas": ["Frutilla albión 1", "Frutilla albión 2", "Frutilla albión 3"],
         "niveles": {"caudal": 2.2, "parametro_cultivo": 5.8}},
    ]),
]


# ---------------------------------------------------------------------------------------------------------------------------
# VALORES REALES POR CULTIVO — informe ES3, Tablas 12 (lechuga), 13 (albahaca), 14 (tomate cherry) y 15 (frutilla).
#   (mín, máx)  = el informe da un RANGO: la simulación oscila dentro de él y la alerta salta al salir (mínimo -> «bajo», máximo -> «alto»).
#   un número   = el informe da UN valor: es la base de la simulación y las reglas de ese sensor siguen siendo PROVISIONALES
#                 (porcentaje sobre el valor), porque el informe no da un rango para alertar. Quedan marcadas con «# único».
# Unidades: pH, EC mS/cm, temperaturas °C, humedad %, fotoperiodo h/día, PPFD µmol/m²/s, N-P-K mg/L, consumo mL/planta/día.
# ---------------------------------------------------------------------------------------------------------------------------
REFERENCIAS = {
    "Lechuga": {"ph": (5.0, 7.0), "nutrientes": (1.5, 2.5), "temperatura": (18, 25), "temperatura_ambiente": (18, 27), "humedad": (45, 80),
                "fotoperiodo": (14, 17), "luz_intensidad": (150, 250), "nitrogeno": 200, "fosforo": 50, "potasio": 300,   # N-P-K único
                "consumo_hidrico": (75, 100)},                       # el 144,5 del cultivo «Milena» es una medición puntual, no el rango
    "Albahaca": {"ph": (5.5, 6.5), "nutrientes": (1.1, 1.6), "temperatura": (18, 24), "temperatura_ambiente": (18, 30), "humedad": (50, 85),
                 "fotoperiodo": 16, "luz_intensidad": (80, 250), "nitrogeno": 210, "fosforo": 80, "potasio": 275,         # fotoperiodo y N-P-K únicos
                 "consumo_hidrico": 502},                            # único: es el máximo diario del periodo de 42 a 48 días
    "Tomate cherry": {"ph": (5.5, 6.5), "nutrientes": 1.6, "temperatura": (18, 27), "humedad": (50, 80),
                      "temperatura_ambiente": 22,                    # sin rango en el informe: 22 °C es la temperatura de día de plántula
                      "fotoperiodo": 15, "luz_intensidad": (50, 200), "nitrogeno": 225, "fosforo": 50, "potasio": 321,    # único
                      "consumo_hidrico": (603.2, 767.6)},            # EC 1,6 = crecimiento vegetativo; la de fructificación (3,2–4,2) no es un límite universal
    "Frutilla": {"ph": 6.0, "nutrientes": 1.8, "temperatura": (18, 30), "temperatura_ambiente": (18, 30), "humedad": (40, 80),
                 "fotoperiodo": (12, 16), "luz_intensidad": (115, 350), "nitrogeno": 207, "fosforo": 65, "potasio": 184,   # pH, EC y N-P-K únicos (etapa vegetativa)
                 "consumo_hidrico": 215.9},                          # único: promedio del informe (el rango 50,2–390 es de variación diaria)
}
# Variables que el informe no cubre (nivel del depósito y gases): siguen con su valor de demostración y no son datos agronómicos.
SIN_FUENTE = {"nivel_agua": 80, "co2": 750, "amoniaco": 2.0}
# Tipos de sensor que el informe sí cubre (su base y su historial se rehacen al pasar a los valores reales).
CON_REFERENCIA = {"ph", "nutrientes", "temperatura", "temperatura_ambiente", "humedad", "fotoperiodo", "luz_intensidad",
                  "nitrogeno", "fosforo", "potasio", "consumo_hidrico"}


def _rango(nombre, tipo):
    """(mín, máx) del informe para ese cultivo y variable, o None si el informe da un solo valor o ninguno."""
    r = REFERENCIAS.get(nombre, {}).get(tipo)
    return tuple(r) if isinstance(r, tuple) else None


def base_y_rango(nombre, tipo, niveles):
    """(valor base de la simulación, rango o None) del sensor `tipo` del cultivo `nombre`. La base es el centro del rango del informe,
    o el valor único del informe; lo que el informe no trae sale de `niveles` (caudal, parámetro) o de SIN_FUENTE."""
    ref = REFERENCIAS.get(nombre, {}).get(tipo)
    if isinstance(ref, tuple):
        return round((ref[0] + ref[1]) / 2, 3), ref
    if ref is not None:
        return ref, None
    return (niveles[tipo] if tipo in niveles else SIN_FUENTE[tipo]), None


def niveles_completos(nombre, n):
    """Nivel normal de TODOS los sensores de un cultivo (los del informe, los de `n` y los de SIN_FUENTE)."""
    return {tipo: base_y_rango(nombre, tipo, n)[0] for tipo, *_ in TIPOS if tipo in CON_REFERENCIA or tipo in n or tipo in SIN_FUENTE}


def _historial_24h(base, rango, k, ahora):
    """144 mediciones NORMALES (una cada 10 min, 24 h) de un sensor: [(t_ms, valor)]. Usa la misma función que el simulador, así que el
    histórico y las lecturas en vivo tienen el mismo comportamiento y el mismo rango."""
    normal = _modulo_simulador().valor_normal
    return [(ahora - 300_000 - 600_000 * (143 - m), round(normal(base, rango, math.sin((m + k * 7) / 14), random.random() - 0.5), 3))
            for m in range(144)]


def _reglas_del_cultivo(nombre, niveles):
    """Umbrales propios del cultivo: (sensor, nombre, operador, umbral[, severidad]). Severidad: «advertencia» (por defecto) o «emergencia».
    - Donde el informe da un RANGO (REFERENCIAS) el umbral es el límite del rango: por debajo del mínimo y por encima del máximo.
    - Donde el informe da un solo valor, la regla es provisional (±% sobre ese valor) hasta validar un rango con la contraparte.
    - Los niveles de agua ≤10 % y ≤5 % son los de la app de prueba AquaBrain_2026 analizada en el ES1 (umbrales operacionales del depósito,
      no requerimientos del cultivo); caudal, CO₂ y amoníaco no están en el informe y conservan su regla de demostración."""
    n = niveles_completos(nombre, niveles)

    def limites(tipo, provisional):
        return _rango(nombre, tipo) or provisional(n[tipo])

    nut = limites("nutrientes", lambda b: (round(b * 0.75, 2), round(b * 1.25, 2)))       # EC mS/cm
    foto = limites("fotoperiodo", lambda b: (b - 4, b + 4))
    luz = limites("luz_intensidad", lambda b: (round(b * 0.6, 1), round(b * 1.5, 1)))     # PPFD
    ph = limites("ph", lambda b: (round(b - 0.8, 1), round(b + 0.8, 1)))
    t_sol = limites("temperatura", lambda b: (15, 27))
    hum = limites("humedad", lambda b: (40, 85))
    t_amb = limites("temperatura_ambiente", lambda b: (None, 32))                         # sin mínimo: lo cubre «riesgo de helada»
    reglas = [("CAU1", f"{nombre}: caudal bajo", "<", round(n["caudal"] * 0.6, 2)),
              ("CAU1", f"{nombre}: caudal alto", ">", round(n["caudal"] * 1.6, 2)),
              ("NUT1", f"{nombre}: nutrientes bajos", "<", nut[0]),
              ("NUT1", f"{nombre}: nutrientes altos", ">", nut[1]),
              ("FOT1", f"{nombre}: exceso de luz", ">", foto[1]),
              ("FOT1", f"{nombre}: pocas horas de luz", "<", foto[0]),
              ("NIV1", f"{nombre}: nivel de agua bajo", "<", 40),
              ("NIV1", f"{nombre}: exceso de agua (desborde)", ">", 95),
              ("NIV1", f"{nombre}: nivel de agua crítico (≤10 %)", "<=", 10, "advertencia"),
              ("NIV1", f"{nombre}: emergencia de agua (≤5 %)", "<=", 5, "emergencia"),
              ("LUZ1", f"{nombre}: falta de luz solar", "<", luz[0]),
              ("LUZ1", f"{nombre}: exceso de luz solar", ">", luz[1]),
              ("NIT1", f"{nombre}: falta de nitrógeno (N)", "<", round(n["nitrogeno"] * 0.7)),        # valor único: provisional
              ("FOS1", f"{nombre}: falta de fósforo (P)", "<", round(n["fosforo"] * 0.7)),
              ("POT1", f"{nombre}: falta de potasio (K)", "<", round(n["potasio"] * 0.7)),
              ("PH1", f"{nombre}: pH demasiado ácido", "<", ph[0]),
              ("PH1", f"{nombre}: pH demasiado alcalino", ">", ph[1]),
              ("CO21", f"{nombre}: gas contaminante CO₂ alto", ">", 1200),
              ("NH31", f"{nombre}: gas contaminante amoníaco alto", ">", 10),
              ("TEM1", f"{nombre}: temperatura de la solución alta", ">", t_sol[1]),
              ("TEM1", f"{nombre}: temperatura de la solución baja", "<", t_sol[0]),
              ("HUM1", f"{nombre}: humedad ambiental baja", "<", hum[0]),
              ("HUM1", f"{nombre}: humedad ambiental alta", ">", hum[1]),
              ("TAM1", f"{nombre}: temperatura ambiente alta", ">", t_amb[1]),
              ("TAM1", f"{nombre}: riesgo de helada (temperatura ambiente baja)", "<", 5, "emergencia")]
    if t_amb[0] is not None:
        reglas.append(("TAM1", f"{nombre}: temperatura ambiente baja", "<", t_amb[0]))
    return reglas


def sembrar():
    """Escribe todo el catálogo y 24 h de histórico. Devuelve (credenciales, dispositivos_para_el_simulador)."""
    ahora = ahora_ms()
    mapa = {f"parametros/{k}": {"valor": v[0]} for k, v in repo.PARAMETROS_INICIALES.items()}
    nombres, unidades = {}, {}
    for codigo, nombre, unidad, vmin, vmax in TIPOS:
        mapa[f"tipos_sensor/{codigo}"] = {"codigo": codigo, "nombre": nombre, "tipo_variable": codigo, "unidad": unidad,
                                          **({"valor_min": vmin} if vmin is not None else {}), **({"valor_max": vmax} if vmax is not None else {})}
        nombres[codigo], unidades[codigo] = nombre, unidad

    dispositivos_demo, contadores, ids_inst = [], {}, {}
    for k, (codigo_inst, nombre_inst, desc_inst, cultivos) in enumerate(INSTALACIONES):
        iid = repo.nuevo_id()
        ids_inst[codigo_inst] = iid
        mapa[f"instalaciones/{iid}"] = {"codigo": codigo_inst, "nombre": nombre_inst, "descripcion": desc_inst, "activa": True,
                                        "ubicacion": UBICACIONES.get(codigo_inst, ""), "creada_en": ahora}
        for c in cultivos:
            cid, did, secreto = repo.nuevo_id(), repo.nuevo_id(), seguridad.token_aleatorio(32)
            mapa[f"cultivos/{cid}"] = {"instalacion_id": iid, "nombre": c["nombre"], "descripcion": c["descripcion"], "activo": True}
            for p, nombre_planta in enumerate(c["plantas"], 1):
                mapa[f"plantas/{repo.nuevo_id()}"] = {"cultivo_id": cid, "codigo": f"P{p}", "nombre": nombre_planta, "activa": True}
            mapa[f"dispositivos/{did}"] = {
                "instalacion_id": iid, "cultivo_id": cid, "codigo": c["dispositivo"], "nombre": f"AquaBrain · {c['nombre']}",
                "secreto_cif": seguridad.cifrar(secreto, f"dispositivo:{did}"), "activo": True, "estado_conexion": "en_linea",
                "ultima_comunicacion": ahora, "calidad_enlace": 85, "creado_en": ahora}
            sensores_sim, ids_sensor = [], {}
            for cod, tipo in SENSORES:
                sid = repo.nuevo_id()
                ids_sensor[cod] = sid
                mapa[f"sensores/{sid}"] = {"dispositivo_id": did, "tipo_sensor_id": tipo, "codigo": cod,
                                           "nombre": f"{nombres[tipo]} · {c['nombre']}", "activo": True}
                base, rango = base_y_rango(c["nombre"], tipo, c["niveles"])      # valor real del informe para ESTE cultivo
                sensores_sim.append({"id_sensor": cod, "tipo_variable": tipo, "unidad": unidades[tipo], "base": base,
                                     "rango": list(rango) if rango else None})
                valor = base
                for t, valor in _historial_24h(base, rango, k, ahora):         # 24 h, una medición cada 10 min
                    mapa[f"mediciones/{sid}/{clave_ms(t)}"] = {"v": valor, "o": "semilla"}
                    hora = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y%m%d%H")
                    contadores[(iid, hora)] = contadores.get((iid, hora), 0) + 1
                mapa[f"ultima/{sid}"] = {"ts": ahora - 300_000, "valor": valor, "conexion": "conectado"}
            for cod_sensor, nombre_regla, op, umbral, *sev in _reglas_del_cultivo(c["nombre"], c["niveles"]):
                mapa[f"reglas/{repo.nuevo_id()}"] = {"nombre": nombre_regla, "sensor_id": ids_sensor[cod_sensor], "operador": op,
                                                     "umbral": umbral, "activa": True, **({"severidad": sev[0]} if sev else {})}
            dispositivos_demo.append({"id_sistema": codigo_inst, "id_dispositivo": c["dispositivo"], "secreto": secreto,
                                      "cultivo": c["nombre"], "sensores": sensores_sim})
    for (iid, hora), n in contadores.items():
        mapa[f"contadores/{iid}/{hora}"] = n

    claves = list(mapa)
    for i in range(0, len(claves), 1000):                              # lotes: una escritura atómica por cada 1000 rutas
        rtdb().update({c: mapa[c] for c in claves[i:i + 1000]})

    credenciales = []
    for email, nombre, rol, insts in [("admin@aquaplants.demo", "Administrador Demo", "administrador", []),
                                      ("supervisor@aquaplants.demo", "Supervisor Demo", "supervisor", []),
                                      ("encargado@aquaplants.demo", "Encargado Demo", "encargado_cultivo", ["INV-NORTE"]),
                                      ("tecnico@aquaplants.demo", "Técnico Demo (servicio técnico)", "tecnico", [])]:
        pw = acceso.clave_demo() or seguridad.generar_password_demo()
        repo.crear_usuario(email, nombre, rol, seguridad.hash_password(pw), [ids_inst[i] for i in insts])
        credenciales.append({"email": email, "rol": rol, "password": pw})
    repo.invalidar()
    return credenciales, dispositivos_demo


def completar_sensores():
    """Agrega, SIN borrar nada, los sensores y reglas nuevos (agua, luz, N-P-K, pH, gases) a una base ya sembrada.
    Idempotente: lo que ya existe no se repite. También actualiza data/dispositivos-demo.json para que la simulación los envíe.
    Devuelve cuántos registros agregó."""
    if not rtdb().get("usuarios"):
        return 0
    repo.invalidar("dispositivos", "sensores", "reglas", "tipos_sensor")
    por_codigo = {c["dispositivo"]: (c["nombre"], c["niveles"], k) for k, (_, _, _, cultivos) in enumerate(INSTALACIONES) for c in cultivos}
    info_tipo = {t[0]: t for t in TIPOS}
    tipos, sensores, reglas = rtdb().get("tipos_sensor") or {}, rtdb().get("sensores") or {}, rtdb().get("reglas") or {}
    ahora, mapa, agregados, contadores, sim = ahora_ms(), {}, 0, {}, {}
    for codigo, nombre, unidad, vmin, vmax in TIPOS:
        if codigo not in tipos:
            mapa[f"tipos_sensor/{codigo}"] = {"codigo": codigo, "nombre": nombre, "tipo_variable": codigo, "unidad": unidad,
                                              **({"valor_min": vmin} if vmin is not None else {}), **({"valor_max": vmax} if vmax is not None else {})}
            agregados += 1
    for iid_, inst in (rtdb().get("instalaciones") or {}).items():              # RF-01: ubicación de la instalación
        if isinstance(inst, dict) and not inst.get("ubicacion") and inst.get("codigo") in UBICACIONES:
            mapa[f"instalaciones/{iid_}/ubicacion"] = UBICACIONES[inst["codigo"]]
            agregados += 1
    for did, d in (rtdb().get("dispositivos") or {}).items():
        if not isinstance(d, dict) or d.get("codigo") not in por_codigo or not d.get("instalacion_id"):
            continue
        cultivo, niveles, k = por_codigo[d["codigo"]]
        iid = d["instalacion_id"]
        ids = {sv.get("codigo"): sid for sid, sv in sensores.items() if sv.get("dispositivo_id") == did}
        for cod, tipo in SENSORES:
            base, rango = base_y_rango(cultivo, tipo, niveles)
            if cod not in ids:
                sid = repo.nuevo_id()
                ids[cod] = sid
                mapa[f"sensores/{sid}"] = {"dispositivo_id": did, "tipo_sensor_id": tipo, "codigo": cod,
                                           "nombre": f"{info_tipo[tipo][1]} · {cultivo}", "activo": True}
                valor = base
                for t, valor in _historial_24h(base, rango, k, ahora):  # 24 h de histórico, una medición cada 10 min
                    mapa[f"mediciones/{sid}/{clave_ms(t)}"] = {"v": valor, "o": "semilla"}
                    hora = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y%m%d%H")
                    contadores[(iid, hora)] = contadores.get((iid, hora), 0) + 1
                mapa[f"ultima/{sid}"] = {"ts": ahora - 300_000, "valor": valor, "conexion": "conectado"}
                agregados += 1
            sim.setdefault(d["codigo"], []).append({"id_sensor": cod, "tipo_variable": tipo, "unidad": info_tipo[tipo][2], "base": base,
                                                    "rango": list(rango) if rango else None})
        hechas = {(r.get("sensor_id"), r.get("nombre")) for r in reglas.values()}
        for cod_sensor, nombre_regla, op, umbral, *sev in _reglas_del_cultivo(cultivo, niveles):
            if (ids[cod_sensor], nombre_regla) not in hechas:
                mapa[f"reglas/{repo.nuevo_id()}"] = {"nombre": nombre_regla, "sensor_id": ids[cod_sensor], "operador": op,
                                                     "umbral": umbral, "activa": True, **({"severidad": sev[0]} if sev else {})}
                agregados += 1
    for (iid, hora), n in contadores.items():
        mapa[f"contadores/{iid}/{hora}"] = {".sv": {"increment": n}}
    claves = list(mapa)
    for i in range(0, len(claves), 1000):
        rtdb().update({c: mapa[c] for c in claves[i:i + 1000]})
    archivo = Path(settings.AQ["DATA_DIR"]) / "dispositivos-demo.json"
    if sim and archivo.exists():                                       # la simulación debe enviar también los sensores nuevos
        config = json.loads(archivo.read_text(encoding="utf-8"))
        cambio = False
        for dev in config:
            tengo = {x["id_sensor"] for x in dev.get("sensores", [])}
            for x in sim.get(dev.get("id_dispositivo"), []):
                if x["id_sensor"] not in tengo:
                    dev.setdefault("sensores", []).append(x)
                    cambio = True
        if cambio:
            archivo.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    repo.invalidar()
    return agregados


def aplicar_valores_reales():
    """Pone al día una base YA sembrada con los valores reales del informe (REFERENCIAS). Se llama sola al iniciar `runserver`.
    Hace, en este orden (y SIN tocar usuarios, alertas ni dispositivos):
      1. borra el histórico de los sensores que el informe cubre y lo rehace con valores normales de SU cultivo (si no, las gráficas
         mezclarían 700 ppm con 1,8 mS/cm), y deja la «última lectura» en ese valor;
      2. actualiza nombre y umbral de las reglas que ya existían y crea las que faltan (p. ej. «temperatura ambiente baja»);
      3. actualiza data/dispositivos-demo.json (unidad, base y rango de cada sensor) para que la simulación envíe los valores reales;
      4. al final cambia nombre y unidad de los tipos de sensor: esa unidad es la marca de «ya migrada», así que una interrupción a la
         mitad se reintenta en el siguiente arranque.
    Los umbrales editados desde la consola solo se reemplazan en la primera migración; después se respetan. Idempotente: devuelve
    cuántos registros cambió (0 si ya estaba al día)."""
    if not rtdb().get("usuarios"):
        return 0
    repo.invalidar("dispositivos", "sensores", "reglas", "tipos_sensor")
    info_tipo = {t[0]: t for t in TIPOS}
    tipos, sensores, reglas = rtdb().get("tipos_sensor") or {}, rtdb().get("sensores") or {}, rtdb().get("reglas") or {}
    pendiente = any((tipos.get(t[0]) or {}).get("unidad") != t[2] or (tipos.get(t[0]) or {}).get("nombre") != t[1] for t in TIPOS if t[0] in tipos)
    por_codigo = {c["dispositivo"]: (c["nombre"], c["niveles"], k) for k, (_, _, _, cultivos) in enumerate(INSTALACIONES) for c in cultivos}
    ahora, borrar, mapa, cambios = ahora_ms(), {}, {}, 0
    sim = {}
    for did, d in (rtdb().get("dispositivos") or {}).items():
        if not isinstance(d, dict) or d.get("codigo") not in por_codigo:
            continue
        cultivo, niveles, k = por_codigo[d["codigo"]]
        ids = {sv.get("codigo"): sid for sid, sv in sensores.items() if sv.get("dispositivo_id") == did}
        for cod, tipo in SENSORES:
            base, rango = base_y_rango(cultivo, tipo, niveles)
            sim.setdefault(d["codigo"], []).append({"id_sensor": cod, "tipo_variable": tipo, "unidad": info_tipo[tipo][2], "base": base,
                                                    "rango": list(rango) if rango else None})
            sid = ids.get(cod)
            if not sid or not pendiente or tipo not in CON_REFERENCIA:
                continue
            borrar[f"mediciones/{sid}"] = None                                         # 1. histórico a escala vieja
            valor = base
            for t, valor in _historial_24h(base, rango, k, ahora):
                mapa[f"mediciones/{sid}/{clave_ms(t)}"] = {"v": valor, "o": "semilla"}
            mapa[f"ultima/{sid}"] = {"ts": ahora - 300_000, "valor": valor, "conexion": "conectado"}
            mapa[f"sensores/{sid}/nombre"] = f"{info_tipo[tipo][1]} · {cultivo}"
            cambios += 1
        existentes = {(r.get("sensor_id"), r.get("nombre")): rid for rid, r in reglas.items()}       # 2. reglas
        for cod_sensor, nombre_regla, op, umbral, *sev in _reglas_del_cultivo(cultivo, niveles):
            if cod_sensor not in ids:
                continue
            rid = existentes.get((ids[cod_sensor], nombre_regla))
            if rid is None:
                mapa[f"reglas/{repo.nuevo_id()}"] = {"nombre": nombre_regla, "sensor_id": ids[cod_sensor], "operador": op, "umbral": umbral,
                                                     "activa": True, **({"severidad": sev[0]} if sev else {})}
                cambios += 1
            elif pendiente and (reglas[rid].get("operador"), reglas[rid].get("umbral")) != (op, umbral):
                mapa[f"reglas/{rid}/operador"], mapa[f"reglas/{rid}/umbral"] = op, umbral
                cambios += 1
    archivo = Path(settings.AQ["DATA_DIR"]) / "dispositivos-demo.json"                 # 3. la simulación envía los valores reales
    if sim and archivo.exists():
        config = json.loads(archivo.read_text(encoding="utf-8"))
        cambio = False
        for dev in config:
            reales = {x["id_sensor"]: x for x in sim.get(dev.get("id_dispositivo"), [])}
            for x in dev.get("sensores", []):
                nuevo = reales.get(x["id_sensor"])
                if nuevo and (x.get("unidad"), x.get("base"), x.get("rango")) != (nuevo["unidad"], nuevo["base"], nuevo["rango"]):
                    x.update(unidad=nuevo["unidad"], base=nuevo["base"], rango=nuevo["rango"])
                    cambio = True
        if cambio:
            archivo.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    if borrar:
        rtdb().update(borrar)
    claves = list(mapa)
    for i in range(0, len(claves), 1000):
        rtdb().update({c: mapa[c] for c in claves[i:i + 1000]})
    if pendiente:                                                                      # 4. marca de «ya migrada»
        rtdb().update({f"tipos_sensor/{cod}/{campo}": valor for cod, nombre, unidad, *_ in TIPOS if cod in tipos
                       for campo, valor in (("nombre", nombre), ("unidad", unidad))})
        cambios += 1
    repo.invalidar()
    return cambios


class Command(BaseCommand):
    help = "Crea datos de demostración en la base (solo si está vacía)."

    def handle(self, *args, **options):
        if rtdb().get("usuarios"):
            self.stdout.write("La base ya tiene datos: no se siembra de nuevo.")
            return
        credenciales, dispositivos = sembrar()
        carpeta = Path(settings.AQ["DATA_DIR"])
        for ruta, contenido in ((carpeta / "dispositivos-demo.json", json.dumps(dispositivos, indent=2, ensure_ascii=False)),
                                (carpeta / "credenciales-demo.txt",
                                 "".join(f"{c['rol']}\t{c['email']}\t{c['password']}\n" for c in credenciales))):
            ruta.write_text(contenido, encoding="utf-8")
            try:
                os.chmod(ruta, 0o600)
            except OSError:
                pass
        for vieja in (carpeta / "simulador-local").glob("cola-*.json"):      # colas de una siembra anterior: sus sensores ya no existen
            try:
                vieja.unlink()
            except OSError:
                pass
        self.stdout.write(self.style.SUCCESS("\n=== Credenciales de demostración ==="))
        for c in credenciales:
            self.stdout.write(f"{c['rol']:<18} {c['email']:<30} {c['password']}")
        self.stdout.write(f"\nTambién en {carpeta / 'credenciales-demo.txt'}")
