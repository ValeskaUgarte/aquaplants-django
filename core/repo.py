"""Acceso a los datos del catálogo en Firebase RTDB (mismo modelo que el proyecto original).

Las colecciones son pequeñas: se leen enteras y se guardan en caché unos segundos para no consultar a Firebase en
cada petición. Toda escritura desde este proceso invalida la caché.

  usuarios, instalaciones, cultivos, plantas, tipos_sensor, dispositivos, sensores, reglas, parametros
"""
import re
import threading
import time
import uuid

from core.rtdb import rtdb

_cache, _lock = {}, threading.Lock()
TTL = {"usuarios": 10, "dispositivos": 30}   # la ingesta y el monitor mantienen «dispositivos» al día en memoria (parchar)
PARAMETROS_INICIALES = {
    "minutos_sin_comunicacion": ("5", "Minutos sin recibir datos para considerar a un dispositivo sin comunicación "
                                      "(valor de demostración, validar con la contraparte)"),
    "google_maps_api_key": ("", "Clave de la API de Google Maps (Maps JavaScript API) para mostrar el mapa de los dispositivos. "
                                "Créala en Google Cloud y restríngela a tu dominio; sin clave se usa un mapa incrustado simple."),
    "retencion_historicos_dias": ("0", "Días de conservación de mediciones; 0 = sin depuración (por validar con la contraparte)"),
    "respaldo_cada_horas": ("24", "Cada cuántas horas se respalda automáticamente la base (0 = desactivado)"),
    "respaldos_conservar": ("7", "Cantidad de respaldos recientes que se guardan (además, uno por semana de las últimas 4 semanas)"),
    "limite_base_mb": ("1024", "Espacio máximo de la base en MB (plan gratuito Spark de Firebase: 1024)"),
    "aviso_espacio_pct": ("80", "Porcentaje de uso del espacio a partir del cual se avisa a los administradores"),
}


def nuevo_id():
    return uuid.uuid4().hex


def col(nombre) -> dict:
    """id -> registro (caché)."""
    ahora = time.time()
    with _lock:
        c = _cache.get(nombre)
        if c and c[0] > ahora:
            return c[1]
    datos = rtdb().get(nombre, {})
    with _lock:
        _cache[nombre] = (ahora + TTL.get(nombre, 15), datos)
    return datos


def lista(nombre) -> list:
    return [{**v, "id": k} for k, v in col(nombre).items()]


def get(nombre, id_):
    v = col(nombre).get(id_)
    return {**v, "id": id_} if v else None


def invalidar(*nombres):
    with _lock:
        for n in (nombres or list(_cache)):
            _cache.pop(n, None)


def parchar(nombre, id_, campos: dict):
    """Aplica en la caché los cambios que este proceso ya escribió en Firebase, sin volver a descargar la colección.
    Un valor None borra el campo. Si la colección no está en caché no hace nada (se leerá completa la próxima vez)."""
    with _lock:
        c = _cache.get(nombre)
        if not c or id_ not in c[1]:
            return
        reg = {**c[1][id_], **campos}
        for k in [k for k, v in campos.items() if v is None]:
            reg.pop(k, None)
        _cache[nombre] = (c[0], {**c[1], id_: reg})


def guardar(nombre, id_, datos):
    rtdb().set(f"{nombre}/{id_}", datos)
    invalidar(nombre)


def actualizar(nombre, id_, campos: dict):
    """Cambia campos sueltos; un valor None borra el campo."""
    rtdb().update({f"{nombre}/{id_}/{k}": v for k, v in campos.items()})
    invalidar(nombre)


def borrar(nombre, id_):
    rtdb().borrar(f"{nombre}/{id_}")
    invalidar(nombre)


def parametro_int(clave, defecto):
    p = col("parametros").get(clave)
    try:
        return int(p["valor"]) if p else defecto
    except (ValueError, KeyError):
        return defecto


# ------------------------------------------------------------------ usuarios
def clave_email(email: str) -> str:
    return re.sub(r"[.#$\[\]/]", ",", email.strip().lower())


def usuario_por_email(email):
    uid = rtdb().get(f"indice_email/{clave_email(email)}")
    if not uid:
        return None
    u = rtdb().get(f"usuarios/{uid}")          # lectura fresca: el bloqueo por intentos no puede depender de una caché
    return {**u, "id": uid} if u else None


def crear_usuario(email, nombre, rol, hash_pw, instalaciones=(), creado_en=None):
    from core.util import ahora_ms
    uid = nuevo_id()
    rtdb().update({
        f"usuarios/{uid}": {"email": email.strip().lower(), "nombre": nombre, "rol": rol, "hash": hash_pw, "activo": True,
                            "intentos_fallidos": 0, "totp_activo": False, "creado_en": creado_en or ahora_ms(),
                            "instalaciones": {i: True for i in instalaciones} or None},
        f"indice_email/{clave_email(email)}": uid})
    invalidar("usuarios")
    return uid
