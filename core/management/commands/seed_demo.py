"""Datos de demostración en Firebase RTDB. TODO es simulado (referencia pedagógica, no son datos reales de AquaPlants).

Regla del proyecto: UN DISPOSITIVO POR CULTIVO. Cada cultivo tiene su dispositivo AquaBrain, sus plantas, sus niveles
de operación y sus propias reglas de alerta (una lechuga y un tomate no necesitan el mismo caudal ni los mismos nutrientes).

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
from core.variables import OCULTAS_POR_DEFECTO, SOLO_SIMULACION
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms

# código, nombre, unidad, límite mínimo, límite máximo de validación
TIPOS = [
    ("caudal", "Caudal", "L/min", 0, None),
    ("consumo_hidrico", "Consumo hídrico", "L", 0, None),
    ("nutrientes", "Nutrientes", "ppm", 0, None),
    ("fotoperiodo", "Fotoperiodo", "h", 0, 24),
    ("parametro_cultivo", "Parámetro de cultivo", "u", None, None),
    # sensores ampliados: agua, luz, nutrientes por elemento, pH y gases contaminantes
    ("nivel_agua", "Nivel de agua", "%", 0, 100),
    ("luz_intensidad", "Intensidad de luz", "klx", 0, None),
    ("nitrogeno", "Nitrógeno (N)", "ppm", 0, None),
    ("fosforo", "Fósforo (P)", "ppm", 0, None),
    ("potasio", "Potasio (K)", "ppm", 0, None),
    ("ph", "pH de la solución", "pH", 0, 14),
    ("co2", "CO₂ ambiental", "ppm", 0, None),
    ("amoniaco", "Amoníaco (NH₃)", "ppm", 0, None),
    ("temperatura", "Temperatura de la solución", "°C", -10, 60),
    # sensores del prototipo AquaBrain analizado en el ES1 (AHT10: temperatura y humedad del ambiente)
    ("humedad", "Humedad ambiental", "%", 0, 100),
    ("temperatura_ambiente", "Temperatura ambiente", "°C", -20, 60),
    # variables del documento de referencias por cultivo (reemplazan a «nutrientes» en ppm y a «intensidad de luz» en klx)
    ("ec", "Conductividad eléctrica (EC)", "mS/cm", 0, 20),
    ("ppfd", "Intensidad fotosintética (PPFD)", "µmol/m²/s", 0, 3000),
]
SENSORES = [("CAU1", "caudal"), ("CON1", "consumo_hidrico"), ("NUT1", "nutrientes"), ("FOT1", "fotoperiodo"), ("PAR1", "parametro_cultivo"),
            ("NIV1", "nivel_agua"), ("LUZ1", "luz_intensidad"), ("NIT1", "nitrogeno"), ("FOS1", "fosforo"), ("POT1", "potasio"),
            ("PH1", "ph"), ("CO21", "co2"), ("NH31", "amoniaco"), ("TEM1", "temperatura"),
            ("HUM1", "humedad"), ("TAM1", "temperatura_ambiente"), ("EC1", "ec"), ("PPF1", "ppfd")]
# Sensores que existen en el equipo pero cuya variable está oculta por defecto en los cultivos: la simulación no los envía
# (ahorra escrituras en Firebase). Si un administrador vuelve a mostrar la variable, un equipo real sí puede enviarla.
SIN_SIMULAR = {cod for cod, tipo in SENSORES if tipo in OCULTAS_POR_DEFECTO}

# Rangos de referencia por cultivo (documento «Reglas de alerta por cultivo», tablas 12 a 15: Shareef et al. 2024, Jones 2014,
# UF/IFAS HS1422 y estudios citados ahí). (mín, máx) = rango; un número solo = valor de referencia.
#   * pH, EC y fotoperiodo con valor único: se alerta fuera de valor ± TOLERANCIA (un valor exacto alertaría siempre por ruido).
#     Lo mismo si el rango es más angosto que esa tolerancia (EC de la frutilla: 1,8 vegetativo y 2,0 fructificación → 1,5–2,3).
#   * N, P y K: valor de referencia de la formulación; se alerta por falta bajo el 70 % (criterio que ya usaba el sistema).
#   * Sin dato en la tabla (temperatura ambiente del tomate cherry): no hay alerta de rango («Sin fuente»).
#   * Tomate cherry, EC: 1,6 (vegetativo) a 4,2 (fructificación). El 3,2–4,2 es una formulación experimental, no un límite universal.
#   * Frutilla, N-P-K: se usan los de crecimiento vegetativo (los de fructificación son 182/82/301 mg/L).
REFERENCIAS = {
    "Lechuga": {"ph": (5.0, 7.0), "ec": (1.5, 2.5), "temperatura": (18, 25), "temperatura_ambiente": (18, 27), "humedad": (45, 80),
                "fotoperiodo": (14, 17), "ppfd": (150, 250), "nitrogeno": 200, "fosforo": 50, "potasio": 300},
    "Albahaca": {"ph": (5.5, 6.5), "ec": (1.1, 1.6), "temperatura": (18, 24), "temperatura_ambiente": (18, 30), "humedad": (50, 85),
                 "fotoperiodo": 16, "ppfd": (80, 250), "nitrogeno": 210, "fosforo": 80, "potasio": 275},
    "Tomate cherry": {"ph": (5.5, 6.5), "ec": (1.6, 4.2), "temperatura": (18, 27), "humedad": (50, 80),
                      "fotoperiodo": 15, "ppfd": (50, 200), "nitrogeno": 225, "fosforo": 50, "potasio": 321},
    "Frutilla": {"ph": 6.0, "ec": (1.8, 2.0), "temperatura": (18, 30), "temperatura_ambiente": (18, 30), "humedad": (40, 80),
                 "fotoperiodo": (12, 16), "ppfd": (115, 350), "nitrogeno": 207, "fosforo": 65, "potasio": 184},
}
TOLERANCIA = {"ph": 0.5, "ec": 0.3, "fotoperiodo": 1}
NUTRIENTES = ("nitrogeno", "fosforo", "potasio")


def limites(variable, ref):
    """(mín, máx) de alerta para una variable con rango; None para N-P-K (solo valor de referencia)."""
    if variable in NUTRIENTES:
        return None
    tol = TOLERANCIA.get(variable, 0)
    if isinstance(ref, (int, float)):
        return (round(ref - tol, 2), round(ref + tol, 2))
    lo, hi = ref
    return (round(lo - tol, 2), round(hi + tol, 2)) if hi - lo < tol else (lo, hi)
# Ubicación de cada instalación (RF-01: nombre y ubicación).
UBICACIONES = {"INV-NORTE": "Sector norte, invernadero 1 (simulado)", "INV-SUR": "Sector sur, invernadero 2 (simulado)"}

# Instalación -> cultivos. Niveles de referencia SIMULADOS por cultivo (caudal L/min, consumo L, nutrientes ppm, luz h/día, parámetro).
INSTALACIONES = [
    ("INV-NORTE", "Invernadero Norte", "Módulo de hojas verdes y aromáticas", [
        {"nombre": "Lechuga", "descripcion": "Lactuca sativa (variedad costina)", "dispositivo": "AQB-01",
         "plantas": ["Lechuga costina 1", "Lechuga costina 2", "Lechuga costina 3", "Lechuga costina 4"],
         "niveles": {"caudal": 2.0, "consumo_hidrico": 0.35, "nutrientes": 700, "fotoperiodo": 14, "parametro_cultivo": 6.0}},
        {"nombre": "Albahaca", "descripcion": "Ocimum basilicum", "dispositivo": "AQB-02",
         "plantas": ["Albahaca genovesa 1", "Albahaca genovesa 2", "Albahaca genovesa 3"],
         "niveles": {"caudal": 2.4, "consumo_hidrico": 0.45, "nutrientes": 900, "fotoperiodo": 16, "parametro_cultivo": 6.2}},
    ]),
    ("INV-SUR", "Invernadero Sur", "Módulo de cultivos de fruto", [
        {"nombre": "Tomate cherry", "descripcion": "Solanum lycopersicum var. cerasiforme", "dispositivo": "AQB-03",
         "plantas": ["Tomate cherry 1", "Tomate cherry 2", "Tomate cherry 3"],
         "niveles": {"caudal": 3.2, "consumo_hidrico": 0.7, "nutrientes": 1500, "fotoperiodo": 14, "parametro_cultivo": 6.0}},
        {"nombre": "Frutilla", "descripcion": "Fragaria × ananassa", "dispositivo": "AQB-04",
         "plantas": ["Frutilla albión 1", "Frutilla albión 2", "Frutilla albión 3"],
         "niveles": {"caudal": 2.2, "consumo_hidrico": 0.4, "nutrientes": 1100, "fotoperiodo": 12, "parametro_cultivo": 5.8}},
    ]),
]


def niveles_completos(n, cultivo=None):
    """Niveles normales de TODOS los sensores de un cultivo. Con referencias (REFERENCIAS), el nivel normal es el centro del rango
    o el valor de referencia; lo demás se deriva de los niveles simulados que ya tenía."""
    base = {**n, "nivel_agua": 80, "luz_intensidad": round(n["fotoperiodo"] * 1.6, 1), "nitrogeno": round(n["nutrientes"] * 0.22),
            "fosforo": round(n["nutrientes"] * 0.07), "potasio": round(n["nutrientes"] * 0.30), "ph": n["parametro_cultivo"],
            "co2": 750, "amoniaco": 2.0, "temperatura": 21.0, "humedad": 65, "temperatura_ambiente": 22.0,
            "ec": round(n["nutrientes"] / 500, 2), "ppfd": 150}
    for variable, ref in REFERENCIAS.get(cultivo, {}).items():
        base[variable] = ref if isinstance(ref, (int, float)) else round((ref[0] + ref[1]) / 2, 2)
    return base


NOMBRES_REGLA = {  # variable: (código del sensor, texto bajo, texto alto)
    "ph": ("PH1", "pH demasiado ácido", "pH demasiado alcalino"),
    "ec": ("EC1", "conductividad eléctrica (EC) baja", "conductividad eléctrica (EC) alta"),
    "temperatura": ("TEM1", "temperatura de la solución baja", "temperatura de la solución alta"),
    "temperatura_ambiente": ("TAM1", "temperatura ambiente baja", "temperatura ambiente alta"),
    "humedad": ("HUM1", "humedad ambiental baja", "humedad ambiental alta"),
    "fotoperiodo": ("FOT1", "pocas horas de luz", "exceso de horas de luz"),
    "ppfd": ("PPF1", "falta de luz (PPFD baja)", "exceso de luz (PPFD alta)"),
}


def _reglas_del_cultivo(nombre, niveles):
    """Umbrales propios del cultivo: (sensor, nombre, operador, umbral[, severidad]).
    Con referencias (REFERENCIAS): una regla bajo el mínimo y otra sobre el máximo de cada variable, y «falta de N/P/K» bajo el 70 %
    del valor de referencia. Caudal, nivel de agua y amoníaco no tienen rango por especie: sus reglas son de SOLO SIMULACIÓN
    (los niveles de agua ≤10 % y ≤5 % son los de la app de prueba AquaBrain_2026 analizada en el ES1). La helada (<5 °C) se mantiene
    como protección general en todos los cultivos."""
    n = niveles_completos(niveles, nombre)
    reglas = [("CAU1", f"{nombre}: caudal bajo", "<", round(n["caudal"] * 0.6, 2)),
              ("CAU1", f"{nombre}: caudal alto", ">", round(n["caudal"] * 1.6, 2)),
              ("NIV1", f"{nombre}: nivel de agua bajo", "<", 40),
              ("NIV1", f"{nombre}: exceso de agua (desborde)", ">", 95),
              ("NIV1", f"{nombre}: nivel de agua crítico (≤10 %)", "<=", 10, "advertencia"),
              ("NIV1", f"{nombre}: emergencia de agua (≤5 %)", "<=", 5, "emergencia"),
              ("NH31", f"{nombre}: gas contaminante amoníaco alto", ">", 10),
              ("TAM1", f"{nombre}: riesgo de helada (temperatura ambiente baja)", "<", 5, "emergencia")]
    refs = REFERENCIAS.get(nombre)
    if refs is None:                     # cultivo sin referencias: los umbrales derivados de sus niveles simulados (como antes)
        return reglas + [
            ("PH1", f"{nombre}: pH demasiado ácido", "<", round(n["ph"] - 0.8, 1)),
            ("PH1", f"{nombre}: pH demasiado alcalino", ">", round(n["ph"] + 0.8, 1)),
            ("FOT1", f"{nombre}: pocas horas de luz", "<", n["fotoperiodo"] - 4),
            ("FOT1", f"{nombre}: exceso de horas de luz", ">", n["fotoperiodo"] + 4),
            ("TEM1", f"{nombre}: temperatura de la solución alta", ">", 27),
            ("TEM1", f"{nombre}: temperatura de la solución baja", "<", 15),
            ("HUM1", f"{nombre}: humedad ambiental baja", "<", 40),
            ("HUM1", f"{nombre}: humedad ambiental alta", ">", 85)] + [
            (cod, f"{nombre}: falta de {txt}", "<", round(n[v] * 0.7)) for v, cod, txt in
            (("nitrogeno", "NIT1", "nitrógeno (N)"), ("fosforo", "FOS1", "fósforo (P)"), ("potasio", "POT1", "potasio (K)"))]
    for variable, ref in refs.items():
        lim = limites(variable, ref)
        if lim is None:
            continue
        cod, bajo, alto = NOMBRES_REGLA[variable]
        reglas += [(cod, f"{nombre}: {bajo}", "<", lim[0]), (cod, f"{nombre}: {alto}", ">", lim[1])]
    for v, cod, txt in (("nitrogeno", "NIT1", "nitrógeno (N)"), ("fosforo", "FOS1", "fósforo (P)"), ("potasio", "POT1", "potasio (K)")):
        if v in refs:
            reglas.append((cod, f"{nombre}: falta de {txt}", "<", round(refs[v] * 0.7)))
    return reglas


def sembrar():
    """Escribe todo el catálogo y 24 h de histórico. Devuelve (credenciales, dispositivos_para_el_simulador)."""
    ahora = ahora_ms()
    mapa = {f"parametros/{k}": {"valor": v[0]} for k, v in repo.PARAMETROS_INICIALES.items()}
    nombres, unidades = {}, {}
    for codigo, nombre, unidad, vmin, vmax in TIPOS:
        mapa[f"tipos_sensor/{codigo}"] = {"codigo": codigo, "nombre": nombre, "tipo_variable": codigo, "unidad": unidad,
                                          **({"simulado": True} if codigo in SOLO_SIMULACION else {}),
                                          **({"valor_min": vmin} if vmin is not None else {}), **({"valor_max": vmax} if vmax is not None else {})}
        nombres[codigo], unidades[codigo] = nombre, unidad
    variables_cultivo = {codigo: True for codigo, *_ in TIPOS if codigo not in OCULTAS_POR_DEFECTO}

    dispositivos_demo, contadores, ids_inst = [], {}, {}
    for k, (codigo_inst, nombre_inst, desc_inst, cultivos) in enumerate(INSTALACIONES):
        iid = repo.nuevo_id()
        ids_inst[codigo_inst] = iid
        mapa[f"instalaciones/{iid}"] = {"codigo": codigo_inst, "nombre": nombre_inst, "descripcion": desc_inst, "activa": True,
                                        "ubicacion": UBICACIONES.get(codigo_inst, ""), "creada_en": ahora}
        for c in cultivos:
            cid, did, secreto = repo.nuevo_id(), repo.nuevo_id(), seguridad.token_aleatorio(32)
            mapa[f"cultivos/{cid}"] = {"instalacion_id": iid, "nombre": c["nombre"], "descripcion": c["descripcion"], "activo": True,
                                       "variables": variables_cultivo}
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
                base = niveles_completos(c["niveles"], c["nombre"])[tipo]
                if cod not in SIN_SIMULAR:
                    sensores_sim.append({"id_sensor": cod, "tipo_variable": tipo, "unidad": unidades[tipo], "base": base})
                valor = base
                for m in range(144):                                   # 24 h, una medición cada 10 min
                    t = ahora - 300_000 - 600_000 * (143 - m)
                    valor = base * (1 + math.sin((m + k * 7) / 14) * 0.08 + (random.random() - 0.5) * 0.04)
                    mapa[f"mediciones/{sid}/{clave_ms(t)}"] = {"v": round(valor, 3), "o": "semilla"}
                    hora = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y%m%d%H")
                    contadores[(iid, hora)] = contadores.get((iid, hora), 0) + 1
                mapa[f"ultima/{sid}"] = {"ts": ahora - 300_000, "valor": round(valor, 3), "conexion": "conectado"}
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
                                              **({"simulado": True} if codigo in SOLO_SIMULACION else {}),
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
        niv, iid = niveles_completos(niveles, cultivo), d["instalacion_id"]
        ids = {sv.get("codigo"): sid for sid, sv in sensores.items() if sv.get("dispositivo_id") == did}
        for cod, tipo in SENSORES:
            base = niv[tipo]
            if cod not in ids:
                sid = repo.nuevo_id()
                ids[cod] = sid
                mapa[f"sensores/{sid}"] = {"dispositivo_id": did, "tipo_sensor_id": tipo, "codigo": cod,
                                           "nombre": f"{info_tipo[tipo][1]} · {cultivo}", "activo": True}
                valor = base
                for m in range(144):                                   # 24 h de histórico, una medición cada 10 min
                    t = ahora - 300_000 - 600_000 * (143 - m)
                    valor = base * (1 + math.sin((m + k * 7) / 14) * 0.08 + (random.random() - 0.5) * 0.04)
                    mapa[f"mediciones/{sid}/{clave_ms(t)}"] = {"v": round(valor, 3), "o": "semilla"}
                    hora = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y%m%d%H")
                    contadores[(iid, hora)] = contadores.get((iid, hora), 0) + 1
                mapa[f"ultima/{sid}"] = {"ts": ahora - 300_000, "valor": round(valor, 3), "conexion": "conectado"}
                agregados += 1
            if cod not in SIN_SIMULAR:
                sim.setdefault(d["codigo"], []).append({"id_sensor": cod, "tipo_variable": tipo, "unidad": info_tipo[tipo][2], "base": base})
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


MARCA_REFERENCIAS = "migraciones/referencias_cultivo_v1"


def aplicar_referencias():
    """Lleva una base ya sembrada a las referencias por cultivo (una sola vez; queda la marca MARCA_REFERENCIAS para no pisar
    después lo que el administrador edite en la consola):
      * cada cultivo recibe su lista de variables visibles (si no tenía),
      * caudal, nivel de agua y amoníaco quedan marcados como «solo simulación»,
      * las reglas de los cultivos de demostración pasan a los rangos de referencia; las que ya no corresponden (nutrientes en ppm,
        luz en klx, CO₂…) se desactivan y sus alertas abiertas se resuelven,
      * la simulación deja de enviar las variables ocultas y usa los niveles normales de cada cultivo.
    Devuelve cuántos cambios hizo."""
    from core import alertas, cache_micro, notificaciones
    if not rtdb().get("usuarios") or rtdb().get(MARCA_REFERENCIAS):
        return 0
    repo.invalidar()
    por_codigo = {c["dispositivo"]: (c["nombre"], c["niveles"]) for _, _, _, cultivos in INSTALACIONES for c in cultivos}
    tipos, cultivos, sensores, reglas = repo.col("tipos_sensor"), repo.col("cultivos"), repo.lista("sensores"), repo.lista("reglas")
    mapa = {}
    for cod in SOLO_SIMULACION:
        if cod in tipos and not tipos[cod].get("simulado"):
            mapa[f"tipos_sensor/{cod}/simulado"] = True
    defecto = {t["tipo_variable"]: True for t in tipos.values()
               if isinstance(t, dict) and t.get("tipo_variable") and t["tipo_variable"] not in OCULTAS_POR_DEFECTO}
    for cid, c in cultivos.items():
        if isinstance(c, dict) and "variables" not in c:
            mapa[f"cultivos/{cid}/variables"] = defecto
    for d in repo.lista("dispositivos"):
        if d.get("codigo") not in por_codigo:
            continue
        nombre, niveles = por_codigo[d["codigo"]]
        ids = {s["codigo"]: s["id"] for s in sensores if s.get("dispositivo_id") == d["id"]}
        deseadas = {}
        for cod, nom, op, umbral, *sev in _reglas_del_cultivo(nombre, niveles):
            if cod in ids:
                deseadas[(ids[cod], nom)] = {"nombre": nom, "sensor_id": ids[cod], "operador": op, "umbral": umbral, "activa": True,
                                             "severidad": sev[0] if sev else "advertencia"}
        for r in [r for r in reglas if r.get("sensor_id") in set(ids.values())]:
            q = deseadas.pop((r["sensor_id"], r.get("nombre")), None)
            if q:
                for campo in ("operador", "umbral", "activa", "severidad"):
                    if r.get(campo, {"activa": True, "severidad": "advertencia"}.get(campo)) != q[campo]:
                        mapa[f"reglas/{r['id']}/{campo}"] = q[campo]
            elif r.get("activa", True):
                mapa[f"reglas/{r['id']}/activa"] = False
                alertas.normalizar(f"u:{r['id']}:{r['sensor_id']}", mapa, "La regla se reemplazó por los rangos de referencia del cultivo.")
        for q in deseadas.values():
            mapa[f"reglas/{repo.nuevo_id()}"] = q
    cambios = len(mapa)
    mapa[MARCA_REFERENCIAS] = ahora_ms()
    claves = list(mapa)
    try:
        for i in range(0, len(claves), 1000):
            rtdb().update({c: mapa[c] for c in claves[i:i + 1000]})
    except Exception:
        alertas.reiniciar()
        notificaciones.descartar_push()
        raise
    notificaciones.despachar_push()
    archivo = Path(settings.AQ["DATA_DIR"]) / "dispositivos-demo.json"
    if archivo.exists():
        config = json.loads(archivo.read_text(encoding="utf-8"))
        for dev in config:
            nombre, niveles = por_codigo.get(dev.get("id_dispositivo"), (None, None))
            niv = niveles_completos(niveles, nombre) if niveles else {}
            dev["sensores"] = [{**x, "base": niv.get(x["tipo_variable"], x.get("base"))} for x in dev.get("sensores", [])
                               if x.get("id_sensor") not in SIN_SIMULAR]
        archivo.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    repo.invalidar()
    cache_micro.limpiar()
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
