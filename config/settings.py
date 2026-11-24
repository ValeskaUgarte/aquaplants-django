"""Configuración de Django para AquaPlants / AquaBrain.

Todo valor sensible llega por variables de entorno (ver .env.example). En producción
(AQ_ENTORNO=produccion) el arranque se niega si falta algo crítico.
"""
import os
import re
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _cargar_env():
    """Lee el archivo `.env` (líneas CLAVE=valor) si existe. Las variables del sistema tienen prioridad."""
    archivo = BASE_DIR / ".env"
    if not archivo.exists():
        return
    for linea in archivo.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and re.fullmatch(r"https://\S+\.(firebaseio\.com|firebasedatabase\.app)/?", linea):
            # Error frecuente: pegar solo la dirección de la base, sin «AQ_FIREBASE_URL=» delante. Antes se ignoraba en silencio
            # y el sistema trabajaba en modo local (por eso nada llegaba a Firebase).
            os.environ.setdefault("AQ_FIREBASE_URL", linea)
            print("AVISO: en .env la dirección de Firebase no tiene el nombre «AQ_FIREBASE_URL=» delante. Se usa igual, pero corrígelo.")
        elif linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()
DATA_DIR = Path(os.environ.get("AQ_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(nombre, defecto=False):
    return os.environ.get(nombre, "1" if defecto else "0").lower() in ("1", "true", "si", "sí", "yes")


def _env_int(nombre, defecto):
    try:
        return int(os.environ.get(nombre, defecto))
    except ValueError as exc:
        raise RuntimeError(f"{nombre} debe ser un entero.") from exc


PRODUCCION = os.environ.get("AQ_ENTORNO", "desarrollo") == "produccion"
DEMO = _env_bool("AQ_DEMO", False)

# --- Reglas duras de producción (mismas que la versión Node) ---
if PRODUCCION and DEMO:
    raise RuntimeError("El modo DEMO no puede activarse en producción.")

# Clave de Django: en desarrollo se genera y guarda localmente con permisos restringidos.
_clave_archivo = DATA_DIR / "django-secret.key"
if os.environ.get("DJANGO_SECRET_KEY"):
    SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
elif PRODUCCION:
    raise RuntimeError("Producción exige DJANGO_SECRET_KEY.")
elif _clave_archivo.exists():
    SECRET_KEY = _clave_archivo.read_text().strip()
else:
    SECRET_KEY = secrets.token_urlsafe(50)
    _clave_archivo.write_text(SECRET_KEY)
    try:
        os.chmod(_clave_archivo, 0o600)
    except OSError:
        pass

DEBUG = not PRODUCCION
# En desarrollo/demostración se aceptan todos los hosts: el celular entra por la dirección del computador, que cambia.
# En producción se exige una lista explícita (AQ_HOSTS).
ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
    "AQ_HOSTS", "localhost,127.0.0.1,[::1],testserver" if PRODUCCION else "*").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("AQ_ORIGENES_CONFIABLES", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "core",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "core.middleware.CabecerasSeguridad",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = []  # La interfaz es React compilado; Django solo sirve la API y ese build.

# No hay base de datos SQL: TODO vive en Firebase Realtime Database (ver core/rtdb.py).
DATABASES = {}
APPEND_SLASH = False  # rutas de la API sin barra final, como en la versión Node
USE_TZ = True
TIME_ZONE = "America/Santiago"
LANGUAGE_CODE = "es"
STATIC_URL = "/assets/"

# --- Interfaces web y móvil (HTML/CSS/JS plano, sin Node) ---
WEB_DIR = BASE_DIR / "web"

# --- Seguridad HTTP ---
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if _env_int("AQ_TRUST_PROXY", 0) else None
SECURE_HSTS_SECONDS = 63072000 if PRODUCCION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUCCION
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = False  # el proxy (Render, nginx) termina TLS
X_FRAME_OPTIONS = "DENY"

# --- Parámetros de AquaPlants ---
AQ = {
    "COOKIE_SESION": "__Host-aq_sid" if PRODUCCION else "aq_sid",
    "TRUST_PROXY": _env_int("AQ_TRUST_PROXY", 0),
    "SESSION_IDLE_MINUTES": _env_int("AQ_SESSION_IDLE_MINUTES", 30),
    "SESSION_ABSOLUTE_HOURS": _env_int("AQ_SESSION_ABSOLUTE_HOURS", 8),
    # Costo de scrypt (N = 2^LOG_N). 15 => 32 MiB por hash. Las pruebas lo bajan.
    "SCRYPT_LOG_N": _env_int("AQ_SCRYPT_LOG_N", 15),
    "LOGIN_MAX_FAILS": _env_int("AQ_LOGIN_MAX_FAILS", 5),
    "LOGIN_LOCK_MINUTES": _env_int("AQ_LOGIN_LOCK_MINUTES", 15),
    "RATE_LOGIN_POR_15MIN": _env_int("AQ_RATE_LOGIN_POR_15MIN", 20),
    "RATE_API_POR_MIN": _env_int("AQ_RATE_API_POR_MIN", 300),
    "RATE_INGEST_POR_MIN": _env_int("AQ_RATE_INGEST_POR_MIN", 240),
    "INGEST_MAX_BATCH": _env_int("AQ_INGEST_MAX_BATCH", 500),
    "DEVICE_CLOCK_SKEW_SECONDS": _env_int("AQ_DEVICE_CLOCK_SKEW_SECONDS", 300),
    "MONITOR_INTERVAL_SECONDS": _env_int("AQ_MONITOR_INTERVAL_SECONDS", 30),
    "CLAVE_CIFRADO_B64": os.environ.get("AQ_APP_ENCRYPTION_KEY", ""),
    "BOOTSTRAP_ADMIN_EMAIL": os.environ.get("AQ_BOOTSTRAP_ADMIN_EMAIL", ""),
    "BOOTSTRAP_ADMIN_PASSWORD": os.environ.get("AQ_BOOTSTRAP_ADMIN_PASSWORD", ""),
    # Firebase Realtime Database. Sin AQ_FIREBASE_URL se usa el modo local compatible (sin internet).
    "FIREBASE_URL": os.environ.get("AQ_FIREBASE_URL", ""),
    # Correo saliente (recuperación de contraseña). Sin AQ_SMTP_HOST el mensaje queda en el registro del servidor (data/correos-salientes.log).
    "SMTP_HOST": os.environ.get("AQ_SMTP_HOST", ""), "SMTP_PORT": _env_int("AQ_SMTP_PORT", 587), "SMTP_USER": os.environ.get("AQ_SMTP_USER", ""),
    "SMTP_PASSWORD": os.environ.get("AQ_SMTP_PASSWORD", ""), "SMTP_DE": os.environ.get("AQ_SMTP_DE", ""), "SMTP_TLS": os.environ.get("AQ_SMTP_TLS", "1") != "0",
    # SOLO PARA DEMOSTRACIONES sin correo: 1 = la pantalla de «Olvidé mi contraseña» muestra el código en pantalla. En producción déjalo en 0.
    "RECUPERACION_DEMO": os.environ.get("AQ_RECUPERACION_DEMO", "0") == "1",
    "URL_PUBLICA": os.environ.get("AQ_URL_PUBLICA", ""),          # dirección por la que el celular alcanza este servidor (se publica en Firebase)
    "TUNEL": os.environ.get("AQ_TUNEL", "1") != "0",              # iniciar un túnel de cloudflared si está instalado
    "RELAY": os.environ.get("AQ_RELAY", "1") != "0",              # la app móvil se conecta a través de Firebase (core/relay.py)
    "PUBLICAR_REGLAS": os.environ.get("AQ_PUBLICAR_REGLAS", "1") != "0",   # publicar firebase/database.rules.json si falta el relé
    "FIREBASE_CREDENCIALES": os.environ.get("AQ_FIREBASE_CREDENCIALES", str(BASE_DIR / "firebase-credenciales.json")),
    "RTDB_LOCAL_ARCHIVO": os.environ.get("AQ_RTDB_LOCAL_ARCHIVO", str(DATA_DIR / "rtdb-local.json")),
    "RTDB_LOCAL_PUERTO": _env_int("AQ_RTDB_LOCAL_PUERTO", 9199),
    "DATA_DIR": DATA_DIR,
    # Carpeta de respaldos de la base (core/respaldo.py). Conviene apuntarla a una carpeta sincronizada (OneDrive, Google Drive).
    "RESPALDOS_DIR": os.environ.get("AQ_RESPALDOS_DIR", str(BASE_DIR / "respaldos")),
    "PRODUCCION": PRODUCCION,
    "DEMO": DEMO,
}

if PRODUCCION and not AQ["CLAVE_CIFRADO_B64"]:
    raise RuntimeError("Producción exige AQ_APP_ENCRYPTION_KEY (32 bytes en base64).")
if PRODUCCION and AQ["SCRYPT_LOG_N"] < 15:
    raise RuntimeError("AQ_SCRYPT_LOG_N debe ser >= 15 en producción.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"consola": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["consola"], "level": "INFO"},
}
