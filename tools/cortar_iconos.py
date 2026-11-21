"""Corta la hoja de iconos de AquaPlants en un PNG por icono (fondo transparente) dentro de web/static/iconos/.

Uso:  python tools/cortar_iconos.py "ruta/a/hoja-de-iconos.png"
La hoja trae 14 iconos de menú (baldosa verde redondeada con su nombre debajo) y la barra inferior con 5 iconos.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

DESTINO = Path(__file__).resolve().parent.parent / "web" / "static" / "iconos"
COLUMNAS = [(80, 320), (366, 606), (652, 892), (940, 1180)]       # x de cada columna de baldosas
FILAS = [10, 272, 535, 798]                                       # y donde empieza cada fila
ALTO_CELDA = 190                                                  # baldosa (sin la sombra larga ni el texto de debajo)
NOMBRES = [
    "bitacora", "comparar", "dispositivos", "servicio-tecnico",
    "ubicacion", "estadisticas", "simular-fallas", "usuarios",
    "reglas-alerta", "instalaciones", "parametros", "equipos-vinculados",
    "auditoria", "informes",
]
BARRA = {  # (x0, x1) de cada icono de la barra inferior; y común
    "tab-hoy": (92, 196), "tab-cultivos": (322, 426), "tab-alertas": (566, 668), "tab-gestion": (802, 926), "tab-cuenta": (1070, 1176),
}
BARRA_Y = (1060, 1162)


def _mascara(rgb, umbral, solo_el_mayor):
    """Todo lo que no es el fondo claro, con los huecos interiores rellenos. Las baldosas conservan solo el cuerpo más grande
    (descarta restos del texto vecino); los iconos de la barra, hechos de varias piezas (casa + hoja), las conservan todas."""
    sin_fondo = rgb.min(axis=2) < umbral
    if solo_el_mayor:
        etiquetas, n = ndimage.label(sin_fondo)
        if n == 0:
            return sin_fondo
        tam = ndimage.sum(sin_fondo, etiquetas, range(1, n + 1))
        sin_fondo = etiquetas == (1 + int(np.argmax(tam)))
    return ndimage.binary_fill_holes(sin_fondo)


def _a_transparente(img, umbral, erosion=1, solo_el_mayor=True):
    rgb = np.array(img.convert("RGB")).astype(int)
    m = _mascara(rgb, umbral, solo_el_mayor)
    if erosion:
        m = ndimage.binary_erosion(m, iterations=erosion)
    alfa = Image.fromarray((m * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.8))
    sal = img.convert("RGBA")
    sal.putalpha(alfa)
    caja = alfa.point(lambda v: 255 if v > 8 else 0).getbbox()
    return sal.crop(caja) if caja else sal


def main(ruta):
    hoja = Image.open(ruta).convert("RGB")
    DESTINO.mkdir(parents=True, exist_ok=True)
    k = 0
    for y in FILAS:
        for x0, x1 in COLUMNAS:
            if k >= len(NOMBRES):
                break
            celda = hoja.crop((x0, y, x1, y + ALTO_CELDA))
            icono = _a_transparente(celda, umbral=236, erosion=2)
            icono.save(DESTINO / f"{NOMBRES[k]}.png", optimize=True)
            k += 1
    for nombre, (x0, x1) in BARRA.items():
        celda = hoja.crop((x0, BARRA_Y[0], x1, BARRA_Y[1]))
        _a_transparente(celda, umbral=225, erosion=0, solo_el_mayor=False).save(DESTINO / f"{nombre}.png", optimize=True)
    print(f"{k + len(BARRA)} iconos en {DESTINO}")


if __name__ == "__main__":
    main(sys.argv[1])
