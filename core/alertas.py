"""Alertas y eventos (misma lógica que el proyecto original).

Estados de una alerta: activa -> reconocida -> revisada -> resuelta. Si la causa desaparece por sí sola (el valor vuelve a la
normalidad o el dispositivo vuelve a comunicarse) la alerta se RESUELVE AUTOMÁTICAMENTE en ese momento, en cualquier estado en que
esté: nadie tiene que ir a revisarla ni confirmarla, y queda en el historial con la marca `resuelta_auto`.
Hay como máximo UNA alerta abierta por clave (regla+sensor, o dispositivo): `alertas_abiertas/{clave}` -> id.
`abrir` y `resolver` NO escriben: agregan cambios al `mapa` que el llamador envía a Firebase de una sola vez.
"""
import re
import secrets
import threading

from core import notificaciones
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms

_lock = threading.RLock()
_abiertas = None


def _cargar():
    global _abiertas
    if _abiertas is None:
        _abiertas = rtdb().get("alertas_abiertas", {})
        _sanear()
    return _abiertas


def _sanear():
    """Si `alertas_abiertas` apunta a una alerta que ya no existe o ya está resuelta, esa clave quedaría bloqueada para siempre
    y el sistema dejaría de generar alertas nuevas de esa regla/sensor. Se limpia al cargar."""
    huerfanas = {}
    for clave, aid in list(_abiertas.items()):
        estado = rtdb().get(f"alertas/{aid}/estado")
        if estado in (None, "resuelta"):
            huerfanas[f"alertas_abiertas/{clave}"] = None
            del _abiertas[clave]
    if huerfanas:
        rtdb().update(huerfanas)


def reiniciar():
    global _abiertas
    with _lock:
        _abiertas = None


def abrir(clave, datos, mapa):
    """Devuelve el id de la alerta creada, o None si ya había una abierta con esa clave."""
    with _lock:
        ab = _cargar()
        if clave in ab:
            return None
        ts = ahora_ms()
        aid = f"{clave_ms(ts)}_{secrets.token_hex(3)}"       # ordenable por fecha: `ultimas()` depende de ello
        ab[clave] = aid
        mapa[f"alertas/{aid}"] = {**datos, "estado": "activa", "creada_en": ts}
        mapa[f"alertas_abiertas/{clave}"] = aid
        notificaciones.por_alerta_nueva(aid, {**datos, "creada_en": ts}, mapa)   # avisa a encargados, supervisores y administradores
        return aid


def normalizar(clave, mapa, detalle=""):
    """La causa desapareció (valor normal o dispositivo reconectado): la alerta se resuelve sola, sin revisión ni confirmación.
    Queda en el historial con una línea de bitácora a nombre del sistema. Devuelve el id de la alerta resuelta, o None si no había."""
    with _lock:
        ab = _cargar()
        aid = ab.pop(clave, None)
        if not aid:
            return None
        ahora = ahora_ms()
        mapa[f"alertas_abiertas/{clave}"] = None
        mapa.update({f"alertas/{aid}/estado": "resuelta", f"alertas/{aid}/resuelta_en": ahora,
                     f"alertas/{aid}/normalizada_en": ahora, f"alertas/{aid}/resuelta_auto": True})
        mapa[f"alertas/{aid}/bitacora/{clave_ms(ahora)}_{secrets.token_hex(2)}"] = {
            "por": "sistema", "rol": "sistema", "accion": "se resolvió automáticamente",
            "nota": detalle or "El valor volvió a la normalidad.", "en": ahora}
        return aid


def cerrar(aid, mapa):
    """Saca la alerta del conjunto de abiertas (alguien con autoridad confirmó o dio la solución)."""
    with _lock:
        ab = _cargar()
        clave = next((k for k, v in ab.items() if v == aid), None)
        if clave:
            del ab[clave]
            mapa[f"alertas_abiertas/{clave}"] = None


def problema_de_regla(nombre_regla):
    """«Lechuga: nivel de agua bajo» -> «Nivel de agua bajo» (título corto de la falla para las pantallas)."""
    t = (nombre_regla or "").split(":", 1)[-1].strip() or "Valor fuera de rango"
    return t if len(t) > 1 and t[1].isupper() else t[:1].upper() + t[1:]   # «pH ácido» se queda igual


def problema(alerta):
    """Título de la falla de una alerta (también para alertas antiguas que no lo guardaron)."""
    if alerta.get("problema"):
        return alerta["problema"]
    if alerta.get("tipo") == "sin_comunicacion":
        return "Dispositivo sin comunicación"
    m = re.search(r'incumple la regla "([^"]+)"', alerta.get("mensaje") or "")
    return problema_de_regla(m.group(1)) if m else "Valor fuera de rango"


def ultimas(n=300):
    """Más recientes primero, con su id."""
    return [{**v, "id": k} for k, v in reversed(rtdb().rango("alertas", ultimos=n))]


def evento(mapa, instalacion_id, dispositivo_id, tipo, descripcion, datos=None):
    """Registra un evento operativo (sin comunicación, reconexión, sincronización diferida, medición rechazada)."""
    ts = ahora_ms()
    mapa[f"eventos/{clave_ms(ts)}_{secrets.token_hex(3)}"] = {
        "instalacion_id": instalacion_id, "dispositivo_id": dispositivo_id or "", "tipo": tipo,
        "descripcion": descripcion, "ocurrido_en": ts, **({"datos": datos} if datos else {})}
