"""Contraseñas, segundo factor (TOTP), sesiones, cifrado de secretos y auditoría encadenada, todo en Firebase RTDB."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from core import repo
from core.rtdb import rtdb
from core.util import ahora_ms, clave_ms


def sha256_hex(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def token_aleatorio(n=32) -> str:
    return secrets.token_urlsafe(n)


def igual_seguro(a, b) -> bool:
    return hmac.compare_digest(str(a).encode(), str(b).encode())


# contraseñas
def hash_password(password: str) -> str:
    log_n = settings.AQ["SCRYPT_LOG_N"]
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=sal, n=2 ** log_n, r=8, p=1, maxmem=2 ** 28, dklen=32)
    return f"scrypt${log_n}${base64.b64encode(sal).decode()}${base64.b64encode(h).decode()}"


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        _, log_n, sal, h = almacenado.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(sal), n=2 ** int(log_n), r=8, p=1,
                              maxmem=2 ** 28, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


_senuelo = None


def gastar_tiempo_verificacion(password: str) -> None:
    """Misma demora exista o no el usuario (evita enumerar cuentas por tiempo de respuesta)."""
    global _senuelo
    if _senuelo is None:
        _senuelo = hash_password("senuelo-no-existe")
    verificar_password(password, _senuelo)


_COMUNES = {"password123456", "123456789012", "qwertyuiopas", "contraseña1234", "aquaplants123",
            "administrador1", "abc123456789", "letmein123456", "welcome12345!"}


def validar_politica_password(password, email: str = "", nombre: str = "") -> list:
    """Política: 12–128 caracteres, mayúscula, minúscula, número y símbolo; sin el correo ni el nombre."""
    errores = []
    if not isinstance(password, str) or len(password) < 12:
        errores.append("Debe tener al menos 12 caracteres.")
    if isinstance(password, str) and len(password) > 128:
        errores.append("Debe tener como máximo 128 caracteres.")
    if not isinstance(password, str):
        return errores
    if not re.search(r"[a-z]", password):
        errores.append("Debe incluir una minúscula.")
    if not re.search(r"[A-Z]", password):
        errores.append("Debe incluir una mayúscula.")
    if not re.search(r"[0-9]", password):
        errores.append("Debe incluir un número.")
    if not re.search(r"[^A-Za-z0-9]", password):
        errores.append("Debe incluir un símbolo.")
    bajo = password.lower()
    local = email.split("@")[0] if email else ""
    if len(local) >= 4 and local.lower() in bajo:
        errores.append("No puede contener tu correo.")
    for parte in [p for p in (nombre or "").lower().split() if len(p) >= 4]:
        if parte in bajo:
            errores.append("No puede contener tu nombre.")
            break
    if bajo in _COMUNES:
        errores.append("Es una contraseña demasiado común.")
    return errores


def generar_password_demo() -> str:
    """Contraseña aleatoria que cumple la política (solo para los usuarios de demostración)."""
    return f"{secrets.token_urlsafe(9)}Aq7!{secrets.choice('#$%&*+=?')}x"


#  cifrado de secretos (dispositivos y TOTP)
_fernet = None


def _clave_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        env = settings.AQ["CLAVE_CIFRADO_B64"]
        if env:
            clave = base64.urlsafe_b64encode(base64.b64decode(env))
        else:
            ruta = settings.AQ["DATA_DIR"] / "clave-cifrado.key"
            if not ruta.exists():
                ruta.write_bytes(Fernet.generate_key())
                try:
                    os.chmod(ruta, 0o600)
                except OSError:
                    pass
            clave = ruta.read_bytes().strip()
        _fernet = Fernet(clave)
    return _fernet


def cifrar(texto: str, contexto: str) -> str:
    return _clave_fernet().encrypt(f"{contexto}|{texto}".encode()).decode()


def descifrar(token: str, contexto: str) -> str:
    try:
        ctx, _, texto = _clave_fernet().decrypt(token.encode()).decode().partition("|")
    except InvalidToken as e:
        raise ValueError("secreto ilegible") from e
    if ctx != contexto:
        raise ValueError("contexto de cifrado inválido")
    return texto


def firmar_dispositivo(secreto: str, timestamp: str, nonce: str, cuerpo: str) -> str:
    return hmac.new(secreto.encode(), f"{timestamp}.{nonce}.{cuerpo}".encode(), hashlib.sha256).hexdigest()


#  segundo factor (TOTP, RFC 6238)
def generar_secreto_totp() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32_decodificar(txt: str) -> bytes:
    t = txt.rstrip("=").upper()
    return base64.b32decode(t + "=" * (-len(t) % 8))


def codigo_totp(secreto_b32: str, instante_ms=None) -> str:
    if instante_ms is None:
        instante_ms = ahora_ms()
    contador = int(instante_ms // 1000 // 30)
    h = hmac.new(_b32_decodificar(secreto_b32), struct.pack(">Q", contador), hashlib.sha1).digest()
    o = h[-1] & 15
    n = ((h[o] & 0x7F) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3]
    return f"{n % 1_000_000:06d}"


def verificar_totp(secreto_b32: str, codigo) -> bool:
    """Acepta la ventana actual ±1 paso (tolerancia de reloj de 30 s)."""
    if not re.fullmatch(r"\d{6}", str(codigo)):
        return False
    ahora = ahora_ms()
    return any(igual_seguro(codigo_totp(secreto_b32, ahora + d * 30000), codigo) for d in (-1, 0, 1))


def uri_otpauth(secreto: str, email: str) -> str:
    return f"otpauth://totp/AquaPlants:{quote(email, safe='')}?secret={secreto}&issuer=AquaPlants&digits=6&period=30"


# sesiones
_cache_sesion = {}          # hash -> (expira_cache, sesion, usuario)
_lock = threading.Lock()


def crear_sesion(usuario, ip, user_agent):
    token, csrf = token_aleatorio(32), token_aleatorio(24)
    ahora, h = ahora_ms(), None
    h = sha256_hex(token)
    rtdb().update({
        f"sesiones/{h}": {"uid": usuario["id"], "csrf": csrf, "creada": ahora, "ultimo": ahora, "ip": ip or "",
                          "ua": (user_agent or "")[:200], "expira": ahora + settings.AQ["SESSION_ABSOLUTE_HOURS"] * 3600_000},
        f"sesiones_usuario/{usuario['id']}/{h}": True})
    return token, csrf


def sesion_valida(token):
    """(sesion, usuario) o (None, None). Caché de 15 s para no consultar Firebase en cada petición."""
    if not token:
        return None, None
    h, ahora = sha256_hex(token), ahora_ms()
    with _lock:
        c = _cache_sesion.get(h)
    if c and c[0] > ahora:
        return c[1], c[2]
    ses = rtdb().get(f"sesiones/{h}")
    if not ses or ses["expira"] < ahora or ahora - ses["ultimo"] > settings.AQ["SESSION_IDLE_MINUTES"] * 60_000:
        if ses:
            cerrar_sesion(token)
        return None, None
    usuario = repo.col("usuarios").get(ses["uid"])
    if not usuario or not usuario.get("activo", True):
        return None, None
    usuario = {**usuario, "id": ses["uid"]}
    if ahora - ses["ultimo"] > 60_000:
        ses["ultimo"] = ahora
        rtdb().set(f"sesiones/{h}/ultimo", ahora)
    with _lock:
        _cache_sesion[h] = (ahora + 15_000, ses, usuario)
    return ses, usuario


def _borrar_sesion(uid, h):
    with _lock:
        _cache_sesion.pop(h, None)
    rtdb().update({f"sesiones/{h}": None, f"sesiones_usuario/{uid}/{h}": None})


def cerrar_sesion(token):
    if not token:
        return
    h = sha256_hex(token)
    ses = rtdb().get(f"sesiones/{h}")
    if ses:
        _borrar_sesion(ses["uid"], h)
    with _lock:
        _cache_sesion.pop(h, None)


def revocar_sesiones_usuario(uid, excepto_token=None):
    """Cierra todas las sesiones del usuario (efecto inmediato), salvo la indicada."""
    conservar = sha256_hex(excepto_token) if excepto_token else None
    for h in rtdb().claves(f"sesiones_usuario/{uid}"):
        if h != conservar:
            _borrar_sesion(uid, h)
    with _lock:
        for h in [k for k, v in _cache_sesion.items() if v[2]["id"] == uid and k != conservar]:
            _cache_sesion.pop(h, None)


#auditoría encadenada
_audit_lock = threading.Lock()
_ultimo_hash = None


def _canonico(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash_registro(prev: str, reg: dict) -> str:
    return sha256_hex(prev + _canonico({k: v for k, v in reg.items() if k not in ("prev", "hash")}))


def registrar_auditoria(*, usuario_id=None, accion, entidad=None, entidad_id=None, ip=None, detalle=None):
    global _ultimo_hash
    with _audit_lock:
        if _ultimo_hash is None:
            ult = rtdb().rango("auditoria", ultimos=1)
            _ultimo_hash = ult[0][1]["hash"] if ult else "GENESIS"
        reg = {"ts": ahora_ms(), "usuario_id": usuario_id or "", "accion": accion, "entidad": entidad or "",
               "entidad_id": str(entidad_id) if entidad_id else "", "ip": ip or "",
               "detalle": _canonico(detalle) if detalle is not None else ""}
        reg["prev"] = _ultimo_hash
        reg["hash"] = _hash_registro(_ultimo_hash, reg)
        rtdb().set(f"auditoria/{clave_ms(reg['ts'])}_{secrets.token_hex(3)}", reg)
        _ultimo_hash = reg["hash"]


def verificar_cadena_auditoria() -> dict:
    previo, filas = "GENESIS", 0
    for clave, r in rtdb().rango("auditoria"):
        if r["prev"] != previo or r["hash"] != _hash_registro(previo, r):
            return {"ok": False, "filaCorrupta": clave}
        previo, filas = r["hash"], filas + 1
    return {"ok": True, "filas": filas}


def invalidar_cache_sesiones():
    with _lock:
        _cache_sesion.clear()


def reiniciar_estado():
    global _ultimo_hash
    _ultimo_hash = None
    with _lock:
        _cache_sesion.clear()
