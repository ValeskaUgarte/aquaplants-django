"""Autenticación: login con bloqueo progresivo, logout, cambio de contraseña, recuperación de contraseña y segundo factor TOTP."""
import hashlib
import hmac
import secrets

from django.conf import settings
from django.http import JsonResponse

from core import correo as correo_saliente, repo, seguridad
from core.rtdb import rtdb
from core.http import (Campo, ErrorApi, correo, invalido, ip_cliente, ruta, sin_contenido, texto, validar)
from core.util import ahora_ms


def _set_cookie(respuesta, token):
    respuesta.set_cookie(settings.AQ["COOKIE_SESION"], token, max_age=settings.AQ["SESSION_ABSOLUTE_HOURS"] * 3600,
                         httponly=True, secure=settings.AQ["PRODUCCION"], samesite="Strict", path="/")


def _publico(u):
    return {"id": u["id"], "email": u["email"], "nombre": u["nombre"], "rol": u["rol"], "totpActivo": bool(u.get("totp_activo"))}


def _credenciales_invalidas():
    return ErrorApi(401, "credenciales_invalidas", "Correo o contraseña incorrectos.")


@ruta(("POST",), sesion=False, limite="login")
def login(request):
    d = validar(request.cuerpo, {"email": Campo(correo), "password": Campo(texto(1, 128)),
                                 "codigo_totp": Campo(texto(6, 6, r"\d{6}"), requerido=False)})
    u = repo.usuario_por_email(d["email"])
    if u is None:
        seguridad.gastar_tiempo_verificacion(d["password"])
        raise _credenciales_invalidas()
    ahora = ahora_ms()
    if u.get("bloqueado_hasta") and u["bloqueado_hasta"] > ahora:
        raise ErrorApi(423, "cuenta_bloqueada", "Cuenta bloqueada temporalmente por intentos fallidos.")

    def falla():
        # Bloqueo con espera creciente: se duplica con cada ciclo de intentos fallidos adicionales.
        maximo = settings.AQ["LOGIN_MAX_FAILS"]
        n = u.get("intentos_fallidos", 0) + 1
        campos = {"intentos_fallidos": n}
        if n >= maximo:
            campos["bloqueado_hasta"] = ahora + settings.AQ["LOGIN_LOCK_MINUTES"] * 2 ** min(n // maximo - 1, 6) * 60_000
        repo.actualizar("usuarios", u["id"], campos)
        seguridad.registrar_auditoria(usuario_id=u["id"], accion="login_fallido", entidad="usuario", entidad_id=u["id"],
                                      ip=ip_cliente(request), detalle={"bloqueada": n >= maximo})
        raise _credenciales_invalidas()

    if not u.get("activo", True) or not seguridad.verificar_password(d["password"], u["hash"]):
        falla()
    if u.get("totp_activo"):
        if not d.get("codigo_totp"):
            raise ErrorApi(401, "totp_requerido", "Ingresa el código de tu aplicación autenticadora.")
        if not seguridad.verificar_totp(seguridad.descifrar(u["totp_secreto_cif"], f"totp:{u['id']}"), d["codigo_totp"]):
            falla()
    repo.actualizar("usuarios", u["id"], {"intentos_fallidos": 0, "bloqueado_hasta": None, "ultimo_login": ahora})
    previo = request.COOKIES.get(settings.AQ["COOKIE_SESION"])
    if previo:
        seguridad.cerrar_sesion(previo)                  # evita la fijación de sesión
    token, csrf = seguridad.crear_sesion(u, ip_cliente(request), request.META.get("HTTP_USER_AGENT", ""))
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="login_ok", entidad="usuario", entidad_id=u["id"], ip=ip_cliente(request))
    respuesta = JsonResponse({"usuario": _publico(u), "csrfToken": csrf})
    _set_cookie(respuesta, token)
    return respuesta


@ruta(("GET",))
def me(request):
    u = repo.usuario_por_email(request.usuario["email"]) or request.usuario      # lectura fresca (estado del 2FA)
    return JsonResponse({"usuario": _publico(u), "csrfToken": request.sesion["csrf"]})


@ruta(("POST",))
def logout(request):
    seguridad.cerrar_sesion(request.token)
    seguridad.registrar_auditoria(usuario_id=request.usuario["id"], accion="logout", ip=ip_cliente(request))
    respuesta = sin_contenido()
    respuesta.delete_cookie(settings.AQ["COOKIE_SESION"], path="/", samesite="Strict")
    return respuesta


@ruta(("POST",), limite="login")
def cambiar_password(request):
    d = validar(request.cuerpo, {"actual": Campo(texto(0, 128)), "nueva": Campo(texto(0, 128))})
    u = repo.usuario_por_email(request.usuario["email"])
    if not seguridad.verificar_password(d["actual"], u["hash"]):
        raise ErrorApi(403, "password_incorrecta", "La contraseña actual no es correcta.")
    errs = seguridad.validar_politica_password(d["nueva"], u["email"], u["nombre"])
    if errs:
        raise invalido("La nueva contraseña no cumple la política.", [{"campo": "nueva", "mensaje": m} for m in errs])
    repo.actualizar("usuarios", u["id"], {"hash": seguridad.hash_password(d["nueva"])})
    seguridad.revocar_sesiones_usuario(u["id"], excepto_token=request.token)       # cierra las demás sesiones
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="password_cambiada", entidad="usuario", entidad_id=u["id"], ip=ip_cliente(request))
    return sin_contenido()


@ruta(("POST",))
def dosfa_iniciar(request):
    u = request.usuario
    secreto = seguridad.generar_secreto_totp()
    repo.actualizar("usuarios", u["id"], {"totp_secreto_cif": seguridad.cifrar(secreto, f"totp:{u['id']}"), "totp_activo": False})
    seguridad.invalidar_cache_sesiones()
    return JsonResponse({"secreto": secreto, "uri": seguridad.uri_otpauth(secreto, u["email"])})


@ruta(("POST",))
def dosfa_activar(request):
    d = validar(request.cuerpo, {"codigo": Campo(texto(6, 6, r"\d{6}"))})
    u = repo.usuario_por_email(request.usuario["email"])
    if not u.get("totp_secreto_cif"):
        raise invalido("Primero inicia la configuración del segundo factor.")
    if not seguridad.verificar_totp(seguridad.descifrar(u["totp_secreto_cif"], f"totp:{u['id']}"), d["codigo"]):
        raise invalido("El código no es correcto.")
    repo.actualizar("usuarios", u["id"], {"totp_activo": True})
    seguridad.invalidar_cache_sesiones()
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="2fa_activado", ip=ip_cliente(request))
    return sin_contenido()


@ruta(("POST",), limite="login")
def dosfa_desactivar(request):
    d = validar(request.cuerpo, {"password": Campo(texto(0, 128)), "codigo": Campo(texto(6, 6, r"\d{6}"))})
    u = repo.usuario_por_email(request.usuario["email"])
    if not u.get("totp_activo"):
        raise invalido("El segundo factor no está activo.")
    if not seguridad.verificar_password(d["password"], u["hash"]) or \
       not seguridad.verificar_totp(seguridad.descifrar(u["totp_secreto_cif"], f"totp:{u['id']}"), d["codigo"]):
        raise ErrorApi(403, "verificacion_fallida", "Contraseña o código incorrectos.")
    repo.actualizar("usuarios", u["id"], {"totp_activo": False, "totp_secreto_cif": None})
    seguridad.invalidar_cache_sesiones()
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="2fa_desactivado", ip=ip_cliente(request))
    return sin_contenido()


# ------------------------------------------------------------------ recuperación de contraseña
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # sin 0/O/1/I: se puede leer y copiar sin errores
VIGENCIA_RECUPERACION_MS = 30 * 60_000
ENTRE_SOLICITUDES_MS = 60_000


def _huella_codigo(codigo):
    limpio = "".join(c for c in str(codigo).upper() if c.isalnum())
    return hmac.new(settings.SECRET_KEY.encode(), limpio.encode(), hashlib.sha256).hexdigest()


@ruta(("POST",), sesion=False, limite="login")
def olvide(request):
    """«Olvidé mi contraseña»: si el correo es de una cuenta activa se genera un código de un solo uso (vale 30 minutos) y se envía por correo.
    La respuesta es SIEMPRE la misma, exista o no la cuenta, para que nadie pueda averiguar qué correos están registrados."""
    d = validar(request.cuerpo, {"email": Campo(correo)})
    respuesta = {"mensaje": "Si el correo está registrado, te enviamos un código para crear una contraseña nueva. Revisa también la carpeta de spam."}
    u = repo.usuario_por_email(d["email"])
    ahora = ahora_ms()
    if not u or not u.get("activo", True) or u.get("eliminado") or ahora - (u.get("recuperacion_ultima") or 0) < ENTRE_SOLICITUDES_MS:
        return JsonResponse(respuesta)
    letras = "".join(secrets.choice(ALFABETO) for _ in range(10))
    codigo = f"AQ-{letras[:5]}-{letras[5:]}"
    rtdb().update({f"recuperacion/{_huella_codigo(codigo)}": {"uid": u["id"], "expira": ahora + VIGENCIA_RECUPERACION_MS}})
    repo.actualizar("usuarios", u["id"], {"recuperacion_ultima": ahora})
    base = settings.AQ.get("URL_PUBLICA") or request.build_absolute_uri("/").rstrip("/")
    enlace = f"{base}/?restablecer={codigo}"
    correo_saliente.enviar(u["email"], "Recupera tu contraseña de AquaPlants",
                           f"Hola {u['nombre']},\n\nRecibimos una solicitud para crear una contraseña nueva en AquaPlants.\n\n"
                           f"Tu código es: {codigo}\n(vale 30 minutos y sirve una sola vez)\n\nPuedes ingresarlo en la pantalla «Olvidé mi contraseña» de la web o de la app, "
                           f"o abrir este enlace:\n{enlace}\n\nSi no fuiste tú, ignora este mensaje: tu contraseña actual sigue igual.\n")
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="recuperacion_solicitada", entidad="usuario", entidad_id=u["id"], ip=ip_cliente(request))
    if settings.AQ.get("RECUPERACION_DEMO"):                     # solo demostraciones sin correo (AQ_RECUPERACION_DEMO=1)
        respuesta.update(codigo_demo=codigo, aviso_demo="Modo demostración: este código normalmente llega solo por correo.")
    return JsonResponse(respuesta)


@ruta(("POST",), sesion=False, limite="login")
def restablecer(request):
    """Crea la contraseña nueva con el código recibido. El código se consume, se levantan los bloqueos y se cierran las sesiones abiertas."""
    d = validar(request.cuerpo, {"codigo": Campo(texto(6, 40)), "nueva": Campo(texto(0, 128))})
    huella = _huella_codigo(d["codigo"])
    reg = rtdb().get(f"recuperacion/{huella}")
    u = repo.get("usuarios", reg["uid"]) if reg else None
    if not reg or reg.get("expira", 0) < ahora_ms() or not u or not u.get("activo", True) or u.get("eliminado"):
        raise ErrorApi(400, "codigo_invalido", "El código no es válido o venció. Pide uno nuevo en «Olvidé mi contraseña».")
    errs = seguridad.validar_politica_password(d["nueva"], u["email"], u["nombre"])
    if errs:
        raise invalido("La nueva contraseña no cumple la política.", [{"campo": "nueva", "mensaje": m} for m in errs])
    repo.actualizar("usuarios", u["id"], {"hash": seguridad.hash_password(d["nueva"]), "intentos_fallidos": 0, "bloqueado_hasta": None})
    rtdb().update({f"recuperacion/{huella}": None})
    seguridad.revocar_sesiones_usuario(u["id"])
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="password_restablecida", entidad="usuario", entidad_id=u["id"], ip=ip_cliente(request))
    return sin_contenido()
