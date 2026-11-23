"""Corta la hoja «Panel de iconos con alertas» (53 iconos: estadísticas, pasos de la visita, problemas en color y acciones) en PNG
transparentes dentro de web/static/iconos/.

Uso:  python tools/cortar_iconos_panel.py "ruta/a/Panel_de_iconos_AquaPlants_con_alertas.png"
Los problemas en color llevan el sufijo -c para convivir con los verdes de cortar_iconos_alertas.py.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

DESTINO = Path(__file__).resolve().parent.parent / "web" / "static" / "iconos"
NOMBRES = [  # orden de lectura de la hoja (8 por fila; la sexta fila trae 5)
    "tiempo-respuesta", "tiempo-resolucion", "soluciones-cuestionadas", "movimientos-equipos", "por-tipo", "por-severidad", "cultivos-mas-alertas", "dispositivos-mas-cortes",
    "abiertas-ahora", "realizadas", "canceladas", "respuesta-media", "resolucion-media", "mantencion-aceptada", "va-en-camino", "llego-al-equipo",
    "mantencion-realizada", "revision-red", "otra-mantencion", "visita-tecnico-2", "cambio-dispositivo", "cancelada", "alerta-detectada-c", "pendiente-c",
    "cerrada", "perdio-comunicacion-c", "emergencia-agua-c", "falta-agua-c", "exceso-agua-c", "falla-bomba-c", "falta-nutrientes-c", "falta-nitrogeno-c",
    "exceso-nutrientes-c", "falta-luz-c", "exceso-luz-c", "gas-amoniaco-c", "gas-co2-c", "ph-acido-c", "ph-alcalino-c", "temperatura-alta-c",
    "humedad-alta-c", "humedad-baja-c", "helada-c", "sin-comunicacion-prolongada-c", "nuevo-dispositivo",
    "resumen-alertas", "estadisticas-2", "ubicacion-equipos", "configuracion", "informes-2", "soporte", "ayuda", "salir",
]


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
        if w >= 85 and h >= 80 and (et[s] == i + 1).sum() > 6500:
            cajas.append((s[1].start, s[0].start, s[1].stop, s[0].stop, 236))
    cajas.sort(key=lambda c: (round(c[1] / 95), c[0]))
    if len(cajas) != len(NOMBRES):
        raise SystemExit(f"Se esperaban {len(NOMBRES)} iconos y se detectaron {len(cajas)}")
    DESTINO.mkdir(parents=True, exist_ok=True)
    for nombre, (x0, y0, x1, y1, umbral) in zip(NOMBRES, cajas):
        _recortar(hoja, (x0, y0, x1, y1), umbral).save(DESTINO / f"{nombre}.png", optimize=True)
    print(f"{len(NOMBRES)} iconos en {DESTINO}")


if __name__ == "__main__":
    main(sys.argv[1])
