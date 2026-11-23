"""Variables que muestra cada cultivo.

Cada cultivo guarda en Firebase la lista de variables que le son relevantes (`cultivos/{id}/variables` = {tipo_variable: true}).
Esa lista se edita desde la consola de administración (Cultivos y plantas) y se respeta en TODOS los paneles: estado actual, comparación,
informes y la app móvil. Una variable oculta en un cultivo:
  * no aparece en los paneles de ese cultivo,
  * no genera alertas en ese cultivo (sus mediciones, si llegan, se guardan igual en el histórico).

Si un cultivo no tiene la lista (bases antiguas o cultivos creados antes de esta función), se muestran todas sus variables.

Por defecto (documento de referencias por cultivo):
  * Con rango de referencia respaldado por fuentes: pH, EC, temperatura de la solución y ambiente, humedad, fotoperiodo, PPFD, N, P, K
    y consumo de agua.
  * Solo simulación (sin rango por especie, pero útiles para demostrar fallas): caudal, nivel de agua y amoníaco.
  * Ocultas: CO₂ (sin fuente), «parámetro de cultivo» (repetía el pH), nutrientes en ppm (reemplazada por EC en mS/cm) e intensidad
    de luz en klx (reemplazada por PPFD).
"""
from core import repo

OCULTAS_POR_DEFECTO = frozenset({"co2", "parametro_cultivo", "nutrientes", "luz_intensidad"})
SOLO_SIMULACION = frozenset({"caudal", "nivel_agua", "amoniaco"})


def por_defecto() -> dict:
    """Lista inicial para un cultivo nuevo: todos los tipos de sensor menos los ocultos por defecto."""
    return {t["tipo_variable"]: True for t in repo.col("tipos_sensor").values()
            if isinstance(t, dict) and t.get("tipo_variable") and t["tipo_variable"] not in OCULTAS_POR_DEFECTO}


def del_cultivo(cultivo_id):
    """Conjunto de variables visibles del cultivo, o None si el cultivo no tiene lista (se muestran todas)."""
    c = repo.col("cultivos").get(cultivo_id) if cultivo_id else None
    v = (c or {}).get("variables")
    if isinstance(v, list):                                   # Firebase puede devolver listas si las claves fueran números
        v = {str(i): x for i, x in enumerate(v) if x is not None}
    return {k for k, activa in v.items() if activa} if isinstance(v, dict) else None


def visible(dispositivo: dict, tipo_variable: str) -> bool:
    vs = del_cultivo((dispositivo or {}).get("cultivo_id"))
    return vs is None or tipo_variable in vs


def en_uso(tipo_variable: str) -> bool:
    """¿Algún cultivo activo muestra esta variable? (para no ofrecer en la comparación variables que nadie mide)."""
    for cid, c in repo.col("cultivos").items():
        if isinstance(c, dict) and c.get("activo", True):
            vs = del_cultivo(cid)
            if vs is None or tipo_variable in vs:
                return True
    return False


def nombre_visible(tipo: dict) -> str:
    """Nombre del tipo para los paneles; las variables de solo simulación lo indican."""
    return f"{tipo['nombre']} (simulación)" if tipo.get("simulado") else tipo["nombre"]
