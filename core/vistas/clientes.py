"""Cliente particular: una persona que compró un dispositivo AquaBrain y lo usa por su cuenta, sin pertenecer a ninguna empresa.

Cómo funciona
  * Cada equipo vendido trae un **código de dispositivo** (el mismo que usa para enviar datos, por ejemplo AQB-05) y una
    **clave de activación** de un solo uso que el administrador genera en Administración > Dispositivos.
  * La persona crea su cuenta con su correo y contraseña, y escribe esos dos datos. El sistema le crea su propia instalación
    («Hogar de …») con su cultivo y le entrega el equipo: desde ese momento ve sus mediciones, recibe sus alertas y puede pedir un técnico.
  * El cliente solo ve lo suyo. Los supervisores de la empresa no ven sus equipos; los técnicos solo ven las solicitudes que el cliente les hace.
  * Más adelante puede activar otros equipos desde «Nuevo dispositivo» (cada uno con su cultivo: un dispositivo por cultivo).
"""
import re
import secrets

from django.http import JsonResponse

from core import repo, seguridad
from core.http import Campo, ErrorApi, conflicto, correo, invalido, ip_cliente, ruta, texto, validar
from core.rtdb import rtdb
from core.util import ahora_ms
from core.vistas.auth import _publico, _set_cookie

ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"            # sin I, O, 0 ni 1 para que no se confundan al escribirlas
PATRON_CODIGO = r"[A-Za-z0-9_-]{2,40}"


def generar_clave_activacion():
    """Clave legible de un solo uso: XXXX-XXXX-XXXX (60 bits)."""
    c = "".join(secrets.choice(ALFABETO) for _ in range(12))
    return f"{c[:4]}-{c[4:8]}-{c[8:]}"


def normalizar_clave(clave):
    return re.sub(r"[^A-Za-z0-9]", "", clave or "").upper()


def hash_clave(clave):
    return seguridad.sha256_hex("activacion:" + normalizar_clave(clave))


def _clave(x):
    if not isinstance(x, str) or not 8 <= len(normalizar_clave(x)) <= 24:
        raise ValueError("La clave de activación no es válida.")
    return x.strip()


def _activacion_invalida():
    return ErrorApi(400, "activacion_invalida", "El código del dispositivo o su clave de activación no son correctos, o ese equipo ya fue activado.")


def _buscar_para_activar(codigo, clave):
    """Dispositivo libre cuyo código y clave de activación coinciden. Mismo error (y mismo tiempo) si algo no cuadra."""
    repo.invalidar("dispositivos")
    disp = next((d for d in repo.lista("dispositivos") if d.get("codigo", "").upper() == codigo.upper()), None)
    if disp is None or not disp.get("clave_activacion_hash"):
        seguridad.gastar_tiempo_verificacion(clave)
        raise _activacion_invalida()
    if not seguridad.igual_seguro(hash_clave(clave), disp["clave_activacion_hash"]) or disp.get("propietario_id") or not disp.get("activo", True):
        raise _activacion_invalida()
    return disp


def _mapa_entrega(disp, uid, iid, nombre_cultivo, ahora):
    """Cambios para entregarle el equipo al cliente: cultivo nuevo en su instalación y dispositivo reasignado."""
    cid = repo.nuevo_id()
    codigo_anterior = repo.col("instalaciones").get(disp["instalacion_id"], {}).get("codigo")
    mapa = {f"cultivos/{cid}": {"instalacion_id": iid, "nombre": nombre_cultivo, "descripcion": "Cultivo del cliente", "activo": True},
            f"dispositivos/{disp['id']}/instalacion_id": iid, f"dispositivos/{disp['id']}/cultivo_id": cid,
            f"dispositivos/{disp['id']}/propietario_id": uid, f"dispositivos/{disp['id']}/activado_en": ahora,
            f"dispositivos/{disp['id']}/clave_activacion_hash": None,
            f"usuarios/{uid}/dispositivos/{disp['id']}": True, f"usuarios/{uid}/instalaciones/{iid}": True}
    if not disp.get("id_sistema_equipo") and codigo_anterior:          # el equipo sigue declarando el código con el que salió de fábrica
        mapa[f"dispositivos/{disp['id']}/id_sistema_equipo"] = codigo_anterior
    return mapa, cid


@ruta(("POST",), sesion=False, limite="login")
def registro(request):
    """Crea la cuenta de un cliente particular y le entrega su equipo. Inicia la sesión al terminar (igual que el ingreso)."""
    d = validar(request.cuerpo, {"email": Campo(correo), "nombre": Campo(texto(2, 120)), "password": Campo(texto(0, 128)),
                                 "codigo_dispositivo": Campo(texto(2, 40, PATRON_CODIGO)), "clave_activacion": Campo(_clave),
                                 "nombre_cultivo": Campo(texto(2, 120), requerido=False), "direccion": Campo(texto(0, 200), requerido=False)})
    errs = seguridad.validar_politica_password(d["password"], d["email"], d["nombre"])
    if errs:
        raise invalido("La contraseña no cumple la política.", [{"campo": "password", "mensaje": m} for m in errs])
    disp = _buscar_para_activar(d["codigo_dispositivo"], d["clave_activacion"])
    if repo.usuario_por_email(d["email"]):
        raise conflicto("Ya existe una cuenta con ese correo. Ingresa con ella y usa «Nuevo dispositivo».")
    ahora, uid, iid = ahora_ms(), repo.nuevo_id(), repo.nuevo_id()
    codigos = {i.get("codigo") for i in repo.col("instalaciones").values()}
    codigo = next(c for c in (f"CLI-{secrets.token_hex(3).upper()}" for _ in range(50)) if c not in codigos)
    nombre = d["nombre"]
    mapa, _ = _mapa_entrega(disp, uid, iid, d.get("nombre_cultivo") or "Mi cultivo", ahora)
    mapa.pop(f"usuarios/{uid}/dispositivos/{disp['id']}"), mapa.pop(f"usuarios/{uid}/instalaciones/{iid}")     # van dentro del registro completo del usuario
    mapa[f"instalaciones/{iid}"] = {"codigo": codigo, "nombre": f"Hogar de {nombre}", "descripcion": "Instalación de un cliente particular",
                                    "ubicacion": d.get("direccion") or "", "activa": True, "creada_en": ahora, "propietario_id": uid}
    mapa[f"usuarios/{uid}"] = {"email": d["email"], "nombre": nombre, "rol": "cliente", "hash": seguridad.hash_password(d["password"]), "activo": True,
                               "intentos_fallidos": 0, "totp_activo": False, "creado_en": ahora, "ultimo_login": ahora,
                               "instalaciones": {iid: True}, "dispositivos": {disp["id"]: True}}
    mapa[f"indice_email/{repo.clave_email(d['email'])}"] = uid
    rtdb().update(mapa)
    repo.invalidar()
    seguridad.invalidar_cache_sesiones()
    u = {**rtdb().get(f"usuarios/{uid}"), "id": uid}
    token, csrf = seguridad.crear_sesion(u, ip_cliente(request), request.META.get("HTTP_USER_AGENT", ""))
    seguridad.registrar_auditoria(usuario_id=uid, accion="cliente_registrado", entidad="usuario", entidad_id=uid, ip=ip_cliente(request),
                                  detalle={"dispositivo": disp["codigo"], "instalacion": codigo})
    respuesta = JsonResponse({"usuario": _publico(u), "csrfToken": csrf}, status=201)
    _set_cookie(respuesta, token)
    return respuesta


@ruta(("POST",), roles=("cliente",), limite="login")
def vincular(request):
    """El cliente activa otro equipo en su instalación (un cultivo nuevo por cada dispositivo)."""
    d = validar(request.cuerpo, {"codigo_dispositivo": Campo(texto(2, 40, PATRON_CODIGO)), "clave_activacion": Campo(_clave),
                                 "nombre_cultivo": Campo(texto(2, 120), requerido=False)})
    u = request.usuario
    iid = next((i for i, x in repo.col("instalaciones").items() if x.get("propietario_id") == u["id"]), None)
    if iid is None:
        raise conflicto("Tu cuenta no tiene una instalación propia.")
    disp = _buscar_para_activar(d["codigo_dispositivo"], d["clave_activacion"])
    n = sum(1 for x in repo.lista("dispositivos") if x["instalacion_id"] == iid) + 1
    mapa, _ = _mapa_entrega(disp, u["id"], iid, d.get("nombre_cultivo") or f"Cultivo {n}", ahora_ms())
    rtdb().update(mapa)
    repo.invalidar()
    seguridad.invalidar_cache_sesiones()
    seguridad.registrar_auditoria(usuario_id=u["id"], accion="cliente_dispositivo_activado", entidad="dispositivo", entidad_id=disp["id"], ip=ip_cliente(request),
                                  detalle={"dispositivo": disp["codigo"]})
    return JsonResponse({"dispositivo": disp["codigo"], "instalacion_id": iid}, status=201)
