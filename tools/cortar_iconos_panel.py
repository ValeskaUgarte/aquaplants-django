"""Corta la hoja «Panel de iconos AquaPlants con alertas» (53 iconos) en PNG transparentes.

Uso:  python tools/cortar_iconos_panel.py "ruta/a/Panel_de_iconos_AquaPlants_con_alertas.png" [carpeta_destino]

Detecta cada baldosa por su forma (no por coordenadas fijas) y las nombra en orden de lectura. Por defecto SOLO agrega los iconos
que todavía no existen en web/static/iconos/ (los que ya estaban quedan tal cual); con la variable AQ_SOBREESCRIBIR=1 los reemplaza.
"""
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cortar_iconos_alertas import _recortar  # noqa: E402

DESTINO = Path(__file__).resolve().parent.parent / "web" / "static" / "iconos"
# orden de lectura de la hoja (8, 8, 8, 8, 8, 5 y 8 por fila)
NOMBRES = [
    "tiempo-respuesta", "tiempo-resolucion", "soluciones-cuestionadas", "movimientos-equipos", "por-tipo", "por-severidad", "cultivos-mas-alertas", "dispositivos-mas-cortes",
    "alertas-abiertas", "realizadas", "canceladas", "respuesta-media", "resolucion-media", "mantencion-aceptada", "en-camino", "llego-equipo",
    "mantencion-realizada", "revision-red", "otra-mantencion", "visita-tecnico", "cambio-dispositivo", "cancelada", "alerta-detectada", "pendiente",
    "cerrada", "perdio-comunicacion", "emergencia-agua", "falta-agua", "exceso-agua", "falla-bomba", "falta-nutrientes", "falta-nitrogeno",
    "exceso-nutrientes", "falta-luz", "exceso-luz", "gas-amoniaco", "gas-co2", "ph-acido", "ph-alcalino", "temperatura-alta",
    "humedad-alta", "humedad-baja", "helada", "sin-comunicacion-prolongada", "nuevo-dispositivo",
    "resumen-alertas", "estadisticas", "ubicacion", "configuracion", "informes", "soporte", "ayuda", "salir",
]


FILAS = (8, 8, 8, 8, 8, 5, 8)


def main(ruta, destino=DESTINO):
    hoja = Image.open(ruta).convert("RGB")
    a = np.array(hoja).astype(int)
    et, _ = ndimage.label(a.min(axis=2) < 236)
    cajas = []
    for i, s in enumerate(ndimage.find_objects(et)):
        h, w = s[0].stop - s[0].start, s[1].stop - s[1].start
        if w >= 90 and h >= 80 and (et[s] == i + 1).sum() > 6000:
            cajas.append((s[1].start, s[0].start, s[1].stop, s[0].stop))
    if len(cajas) != len(NOMBRES):
        raise SystemExit(f"Se esperaban {len(NOMBRES)} iconos y se detectaron {len(cajas)}")
    # Orden de lectura: primero por altura (el centro de cada baldosa) y, dentro de cada fila, de izquierda a derecha.
    cajas.sort(key=lambda c: (c[1] + c[3]) / 2)
    ordenadas, i = [], 0
    for n in FILAS:
        ordenadas += sorted(cajas[i:i + n], key=lambda c: c[0])
        i += n
    cajas = ordenadas
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    nuevos = []
    for nombre, caja in zip(NOMBRES, cajas):
        archivo = destino / f"{nombre}.png"
        if archivo.exists() and not os.environ.get("AQ_SOBREESCRIBIR"):
            continue
        _recortar(hoja, caja, 236).save(archivo, optimize=True)
        nuevos.append(nombre)
    print(f"{len(nuevos)} iconos nuevos en {destino}: {', '.join(nuevos)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else DESTINO)
