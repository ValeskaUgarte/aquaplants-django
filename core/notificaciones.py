"""Notificaciones por usuario (bandeja en Firebase) y aviso «push» al celular.

Quién recibe qué:
  * alerta nueva ............ encargados de esa instalación, supervisores y administradores.
  * encargado reconoce ...... supervisores y administradores («voy a revisar»).
  * encargado registra revisión (con su comentario) ... supervisores (deben confirmar) y administradores.
  * supervisor confirma / rechaza (con su comentario) ... encargados de esa instalación y administradores.
Quien ejecuta la acción nunca se notifica a sí mismo.

Cada notificación vive en `notificaciones/{usuario}/{id}`. Las funciones `por_*` NO escriben: agregan al `mapa` que el
llamador envía a Firebase de una sola vez (misma escritura atómica que la alerta). El aviso push se envía después, si hay
equipos vinculados con token de Firebase Cloud Messaging (ver core/push.py).
"""
import logging
import secrets

from core import repo
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms

log = logging.getLogger("aquaplants.notificaciones")
MAX_POR_USUARIO = 60


def _destinatarios(instalacion_id, *, encargados, supervisores, administradores, excluir=None, dispositivo_id=None):
    """Si el dispositivo tiene responsables asignados, los encargados que reciben el aviso son ellos; si no, los de la instalación."""
    usuarios = [u for u in repo.lista("usuarios") if u.get("activo", True) and not u.get("eliminado") and u["id"] != excluir]
    asignados = {u["id"] for u in usuarios if u.get("rol") == "encargado_cultivo" and dispositivo_id and dispositivo_id in (u.get("dispositivos") or {})}
    salida = []
    for u in usuarios:
        rol = u.get("rol")
        if (rol == "encargado_cultivo" and encargados and ((u["id"] in asignados) if asignados else instalacion_id in (u.get("instalaciones") or {}))) \
                or (rol == "supervisor" and supervisores) or (rol == "administrador" and administradores):
            salida.append(u["id"])
    return salida


def ids_tecnicos(tecnico_id=None, excluir=None):
    """Técnico asignado (conexión directa) o, si no hay, todos los técnicos activos (bolsa de solicitudes)."""
    if tecnico_id:
        return [tecnico_id] if tecnico_id != excluir else []
    return [u["id"] for u in repo.lista("usuarios") if u.get("rol") == "tecnico" and u.get("activo", True) and not u.get("eliminado") and u["id"] != excluir]


def avisar(mapa, uids, tipo, titulo, mensaje, alerta_id="", instalacion_id="", critica=False):
    """Aviso genérico a una lista de usuarios (lo usan las solicitudes técnicas)."""
    _agregar(mapa, list(dict.fromkeys(uids)), tipo, titulo, mensaje, alerta_id, instalacion_id, critica=critica)


def _agregar(mapa, uids, tipo, titulo, mensaje, alerta_id, instalacion_id="", critica=False):
    ts = ahora_ms()
    pendientes = []
    for uid in uids:
        nid = f"{clave_ms(ts)}_{secrets.token_hex(3)}"
        mapa[f"notificaciones/{uid}/{nid}"] = {"tipo": tipo, "titulo": titulo, "mensaje": mensaje, "alerta_id": alerta_id,
                                               "instalacion_id": instalacion_id, "critica": bool(critica), "creada_en": ts, "leida": False}
        pendientes.append(uid)
    if pendientes:
        _por_enviar.append((pendientes, titulo, mensaje, alerta_id))


_por_enviar = []     # (uids, titulo, mensaje, alerta_id): se despachan como push tras escribir en la base


def despachar_push():
    """Llamar DESPUÉS de escribir el mapa en la base. Nunca lanza errores: un fallo del push no debe afectar a la alerta."""
    global _por_enviar
    lote, _por_enviar = _por_enviar, []
    if not lote:
        return
    try:
        from core import push
        for uids, titulo, mensaje, alerta_id in lote:
            push.enviar_en_segundo_plano(uids, titulo, mensaje, alerta_id)
    except Exception:  # noqa: BLE001
        log.exception("No se pudo despachar el aviso push")


def descartar_push():
    """Si la escritura en la base falló, no se avisa de algo que no quedó registrado."""
    _por_enviar.clear()


def por_alerta_nueva(aid, datos, mapa):
    iid = datos.get("instalacion_id", "")
    uids = _destinatarios(iid, encargados=True, supervisores=True, administradores=True, dispositivo_id=datos.get("dispositivo_id"))
    sin_com = datos.get("tipo") == "sin_comunicacion"
    titulo = "Sin comunicación" if sin_com else "EMERGENCIA" if datos.get("severidad") == "emergencia" else "Nueva alerta"
    _agregar(mapa, uids, "alerta_nueva", titulo, datos.get("mensaje", ""), aid, iid, critica=True)


def _quien(usuario):
    rol = {"encargado_cultivo": "encargado de cultivo", "supervisor": "supervisor", "administrador": "administrador"}.get(usuario.get("rol"), "")
    return f"{usuario.get('nombre', 'Alguien')} ({rol})" if rol else usuario.get("nombre", "Alguien")


def _contexto(alerta):
    cultivo = repo.col("cultivos").get(alerta.get("cultivo_id") or "", {}).get("nombre")
    disp = repo.col("dispositivos").get(alerta.get("dispositivo_id") or "", {}).get("codigo")
    return " · ".join(x for x in (cultivo, disp) if x)


def por_reconocida(aid, alerta, usuario, mapa):
    iid = alerta.get("instalacion_id", "")
    uids = _destinatarios(iid, encargados=False, supervisores=True, administradores=True, excluir=usuario["id"], dispositivo_id=alerta.get("dispositivo_id"))
    _agregar(mapa, uids, "alerta_reconocida", "Alerta en revisión",
             f"{_quien(usuario)} fue a revisar. {_contexto(alerta)}".strip(), aid, iid)


def por_revision(aid, alerta, usuario, solucionado, nota, mapa):
    iid = alerta.get("instalacion_id", "")
    uids = _destinatarios(iid, encargados=False, supervisores=True, administradores=True, excluir=usuario["id"], dispositivo_id=alerta.get("dispositivo_id"))
    if solucionado:
        titulo, msg = "Revisión lista: falta confirmar", f"{_quien(usuario)} dice que quedó solucionado. Comentario: {nota}"
    else:
        titulo, msg = "Revisión sin solución", f"{_quien(usuario)} revisó y NO quedó solucionado. Comentario: {nota}"
    _agregar(mapa, uids, "alerta_revisada", titulo, f"{_contexto(alerta)} — {msg}".strip(" —"), aid, iid, critica=not solucionado)


def por_verificacion(aid, alerta, usuario, aprobada, nota, mapa):
    iid = alerta.get("instalacion_id", "")
    uids = _destinatarios(iid, encargados=True, supervisores=False, administradores=True, excluir=usuario["id"], dispositivo_id=alerta.get("dispositivo_id"))
    if aprobada:
        titulo, msg = "Alerta resuelta", f"{_quien(usuario)} confirmó la solución." + (f" Comentario: {nota}" if nota else "")
    else:
        titulo, msg = "Revisión rechazada", f"{_quien(usuario)} rechazó la revisión. Motivo: {nota}"
    _agregar(mapa, uids, "alerta_confirmada" if aprobada else "alerta_rechazada", titulo, f"{_contexto(alerta)} — {msg}".strip(" —"),
             aid, iid, critica=not aprobada)


def por_cuestionada(aid, alerta, motivo, requiere_tecnico, mapa):
    """La medición no respalda la solución informada: se avisa a quien corresponde, con la recomendación de derivar a un técnico."""
    iid = alerta.get("instalacion_id", "")
    uids = _destinatarios(iid, encargados=True, supervisores=True, administradores=True, dispositivo_id=alerta.get("dispositivo_id"))
    msg = f"{motivo}" + (" Se recomienda derivar a un técnico o programar una mantención." if requiere_tecnico else "")
    _agregar(mapa, uids, "solucion_cuestionada", "Solución no confirmada por la medición", f"{_contexto(alerta)} — {msg}".strip(" —"), aid, iid, critica=True)


def por_derivacion(aid, alerta, usuario, tipo_texto, nota, mapa):
    iid = alerta.get("instalacion_id", "")
    uids = _destinatarios(iid, encargados=False, supervisores=True, administradores=True, excluir=usuario["id"], dispositivo_id=alerta.get("dispositivo_id"))
    _agregar(mapa, uids, "alerta_derivada", "Derivada a técnico / mantención",
             f"{_contexto(alerta)} — {_quien(usuario)} derivó la alerta: {tipo_texto}. {nota}".strip(" —"), aid, iid, critica=False)


# ------------------------------------------------------------------ lectura (API)
def listar(uid, limite=40):
    filas = rtdb().rango(f"notificaciones/{uid}", ultimos=limite)
    return [{**v, "id": k} for k, v in reversed(filas)]


def no_leidas(uid):
    return sum(1 for _, v in rtdb().rango(f"notificaciones/{uid}", ultimos=MAX_POR_USUARIO) if not v.get("leida"))


def marcar_leidas(uid, ids=None):
    filas = rtdb().rango(f"notificaciones/{uid}", ultimos=MAX_POR_USUARIO)
    cambios = {f"notificaciones/{uid}/{k}/leida": True for k, v in filas if not v.get("leida") and (ids is None or k in ids)}
    if cambios:
        rtdb().update(cambios)
    return len(cambios)


def depurar(dias=30):
    """Borra notificaciones con más de `dias` días (una vez por hora desde core.monitor)."""
    corte = clave_ms(ahora_ms() - dias * 86_400_000)
    c = rtdb()
    for uid in c.claves("notificaciones"):
        viejas = c.rango(f"notificaciones/{uid}", hasta=corte, primeros=500)
        if viejas:
            c.update({f"notificaciones/{uid}/{k}": None for k, _ in viejas})
