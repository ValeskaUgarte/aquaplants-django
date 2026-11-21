"""Corta la hoja «Panel de Alertas» (38 iconos de problemas, estados y KPI) en PNG transparentes dentro de web/static/iconos/.

Uso:  python tools/cortar_iconos_alertas.py "ruta/a/Panel_de_Alertas_AquaPlants.png"
Detecta cada baldosa verde por su forma (no por coordenadas fijas) y las nombra en orden de lectura.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

DESTINO = Path(__file__).resolve().parent.parent / "web" / "static" / "iconos"
NOMBRES = [  # orden de lectura de la hoja (6, 5, 6, 6, 6 y 9 por fila)
    "perdio-comunicacion", "alerta-detectada", "pendiente", "resueltas", "visita-tecnico", "ver-maps",
    "sensores", "consola-django", "api", "firebase", "terminar-falla",
    "emergencia-agua", "falta-agua", "exceso-agua", "falla-bomba", "falta-nutrientes", "falta-nitrogeno",
    "exceso-nutrientes", "falta-luz", "exceso-luz", "gas-amoniaco", "gas-co2", "ph-acido",
    "ph-alcalino", "temperatura-alta", "humedad-alta", "humedad-baja", "helada", "sin-comunicacion-prolongada",
    "nadie-reviso", "en-revision", "falta-confirmar", "resueltas-24h", "instalaciones-kpi", "dispositivos-en-linea",
    "sin-comunicacion", "alertas-abiertas", "mediciones-24h",
]
EXTRA = [(918, 984, 1044, 1100)]   # baldosas muy pálidas que el umbral normal no ve: (x0, y0, x1, y1)


def _recortar(hoja, caja, umbral):
    x0, y0, x1, y1 = caja
    celda = hoja.crop((x0 - 3, y0 - 3, x1 + 3, y1 + 3))
    rgb = np.array(celda).astype(int)
    minimo = rgb.min(axis=2)
    et, n = ndimage.label(minimo < umbral)
    tam = ndimage.sum(minimo < umbral, et, range(1, n + 1))
    m = et == 1 + int(np.argmax(tam))
    # El verde de la baldosa es muy pálido hacia la izquierda y arriba: se recupera hasta la altura del borde inferior (más abajo solo hay sombra)
    pale = ndimage.binary_dilation(m, iterations=45) & (minimo < 248)
    pale[np.where(m.any(axis=1))[0].max() + 1:, :] = False
    et2, n2 = ndimage.label(m | pale)
    tam2 = ndimage.sum(m | pale, et2, range(1, n2 + 1))
    m = ndimage.binary_fill_holes(et2 == 1 + int(np.argmax(tam2)))
    m = ndimage.binary_erosion(m, iterations=1)
    alfa = Image.fromarray((m * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.8))
    sal = celda.convert("RGBA"); sal.putalpha(alfa)
    return sal.crop(alfa.point(lambda v: 255 if v > 8 else 0).getbbox())


def main(ruta):
    hoja = Image.open(ruta).convert("RGB")
    a = np.array(hoja).astype(int)
    et, n = ndimage.label(a.min(axis=2) < 236)
    cajas = []
    for i, s in enumerate(ndimage.find_objects(et)):
        h, w = s[0].stop - s[0].start, s[1].stop - s[1].start
        if w >= 90 and h >= 90 and (et[s] == i + 1).sum() > 7000:
            cajas.append((s[1].start, s[0].start, s[1].stop, s[0].stop, 236))
    for x0, y0, x1, y1 in EXTRA:
        if not any(abs(c[0] - x0) < 40 and abs(c[1] - y0) < 40 for c in cajas):
            cajas.append((x0, y0, x1, y1, 247))
    cajas.sort(key=lambda c: (round(c[1] / 100), c[0]))
    if len(cajas) != len(NOMBRES):
        raise SystemExit(f"Se esperaban {len(NOMBRES)} iconos y se detectaron {len(cajas)}")
    DESTINO.mkdir(parents=True, exist_ok=True)
    for nombre, (x0, y0, x1, y1, umbral) in zip(NOMBRES, cajas):
        _recortar(hoja, (x0, y0, x1, y1), umbral).save(DESTINO / f"{nombre}.png", optimize=True)
    print(f"{len(NOMBRES)} iconos en {DESTINO}")


if __name__ == "__main__":
    main(sys.argv[1])
