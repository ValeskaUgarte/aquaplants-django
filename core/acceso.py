"""Acceso recuperable: la contraseña de las cuentas de demostración sale del .env, no de un archivo que se pierde.

* AQ_DEMO_PASSWORD  -> contraseña única de las cuentas *@aquaplants.demo (debe cumplir la política de contraseñas).
* AQ_BOOTSTRAP_ADMIN_EMAIL / AQ_BOOTSTRAP_ADMIN_PASSWORD -> administrador propio, se crea si no existe.
* AQ_DESBLOQUEAR_AL_INICIAR (1 por defecto en desarrollo) -> al arrancar el servidor se quitan los bloqueos por intentos fallidos.

`asegurar_acceso()` se ejecuta en cada `runserver`: en desarrollo la clave del .env es la fuente de verdad, así que aunque se
reinicie la base, se resiembre o una cuenta quede bloqueada, basta con volver a arrancar el servidor.
En producción solo se crea el administrador inicial si falta y se desbloquea si se pide; nunca se pisan contraseñas existentes.
"""
import os
import logging

from django.conf import settings

from core import repo, seguridad
from core.rtdb import rtdb

log = logging.getLogger("aquaplants.acceso")
SUFIJO_DEMO = "@aquaplants.demo"


def clave_demo():
    """Contraseña definida en el .env para las cuentas demo, o None (entonces se generan al azar)."""
    pw = os.environ.get("AQ_DEMO_PASSWORD", "").strip()
    if not pw:
        return None
    errs = seguridad.validar_politica_password(pw, "", "")
    if errs:
        log.warning("AQ_DEMO_PASSWORD no cumple la política (%s); se ignora.", " ".join(errs))
        return None
    return pw


def _desbloquear_al_iniciar():
    return os.environ.get("AQ_DESBLOQUEAR_AL_INICIAR", "0" if settings.AQ["PRODUCCION"] else "1") != "0"


def asegurar_acceso():
    """Devuelve la lista de acciones realizadas (texto), para mostrarlas al iniciar."""
    acciones = []
    produccion = settings.AQ["PRODUCCION"]

    # Administrador propio (si está configurado en el .env): se crea si falta.
    email, pw = settings.AQ["BOOTSTRAP_ADMIN_EMAIL"].strip().lower(), settings.AQ["BOOTSTRAP_ADMIN_PASSWORD"]
    if email and pw and not repo.usuario_por_email(email):
        errs = seguridad.validar_politica_password(pw, email, "Administrador")
        if errs:
            log.warning("AQ_BOOTSTRAP_ADMIN_PASSWORD no cumple la política: %s", " ".join(errs))
        else:
            repo.crear_usuario(email, "Administrador", "administrador", seguridad.hash_password(pw), [])
            acciones.append(f"Administrador creado: {email}")

    repo.invalidar("usuarios")
    for uid, u in (rtdb().get("usuarios") or {}).items():
        campos = {}
        correo = u.get("email", "")
        if _desbloquear_al_iniciar() and (u.get("bloqueado_hasta") or u.get("intentos_fallidos")):
            campos.update({"intentos_fallidos": 0, "bloqueado_hasta": None})
            acciones.append(f"Desbloqueado: {correo}")
        if not produccion:
            es_demo = correo.endswith(SUFIJO_DEMO)
            clave = clave_demo() if es_demo else (pw if correo == email and pw else None)
            if clave and not seguridad.verificar_password(clave, u.get("hash", "")):
                campos["hash"] = seguridad.hash_password(clave)
                acciones.append(f"Contraseña sincronizada con el .env: {correo}")
            if (es_demo or correo == email) and u.get("activo") is False:
                campos["activo"] = True
        if campos:
            repo.actualizar("usuarios", uid, campos)
            if "hash" in campos:
                seguridad.revocar_sesiones_usuario(uid)
    if acciones:
        seguridad.invalidar_cache_sesiones()
    return acciones


def reparar_dispositivos():
    """Borra registros «fantasma» de dispositivos (sin código ni instalación): quedan cuando algo escribe el estado de un
    dispositivo que ya no existe (por ejemplo tras reiniciar o resembrar la base con el servidor aún enviando datos).
    Rompían la recepción de datos con `KeyError: 'codigo'`."""
    repo.invalidar("dispositivos")
    fantasmas = [k for k, v in (rtdb().get("dispositivos") or {}).items()
                 if not isinstance(v, dict) or not v.get("codigo") or not v.get("instalacion_id")]
    if fantasmas:
        rtdb().update({f"dispositivos/{k}": None for k in fantasmas})
        repo.invalidar("dispositivos")
    return [f"Registro de dispositivo dañado eliminado: {k}" for k in fantasmas]
