"""Documentación de la API para la web (solo administrador) y estado de la conexión con Firebase.

Nunca entrega claves ni credenciales: de Firebase solo se informa el modo, el nombre del proyecto y el enlace a su consola.
"""
import re

from django.conf import settings
from django.http import JsonResponse

from core.http import ruta
from core.rtdb import rtdb
from core.vistas.operacion import _panel

ADMIN = ("administrador",)
TODOS = "todos los roles"
JEFES = "supervisor y administrador"
SOLICITAN = "encargado, cliente, supervisor y administrador"

# (grupo, [(método, ruta, para qué sirve, quién, ejemplo de consulta o None si no se puede probar desde aquí)])
CATALOGO = [
    ("Estado del sistema", [
        ("GET", "/api/salud", "Chequeo de salud (público, sin datos sensibles).", "público", ""),
        ("GET", "/api/panel", "Instalaciones, cultivos y totales que ve el panel.", TODOS, ""),
        ("GET", "/api/estado-actual", "Última lectura de cada sensor de una instalación.", TODOS, "?instalacion_id="),
    ]),
    ("Mediciones, alertas y eventos", [
        ("GET", "/api/mediciones", "Serie de un sensor (agregación opcional).", TODOS, "?sensor_id=&desde=&hasta=&agregacion="),
        ("GET", "/api/alertas", "Alertas según estado e instalación.", TODOS, "?estado=abiertas"),
        ("POST", "/api/alertas/{id}/reconocer", "Marcar una alerta como en revisión.", "encargado y administrador", None),
        ("POST", "/api/alertas/{id}/derivar", "Pedir un técnico a partir de una alerta.", SOLICITAN, None),
        ("GET", "/api/eventos", "Eventos del sistema.", TODOS, ""),
        ("GET", "/api/comparacion", "Compara cultivos o instalaciones por variable.", JEFES, "?tipo_variable=&agrupar=cultivo"),
        ("GET", "/api/estadisticas", "Tiempos de respuesta, cortes y mantenciones.", TODOS, "?dias=7"),
    ]),
    ("Servicio técnico y ubicación", [
        ("GET", "/api/mantenciones", "Solicitudes y órdenes de trabajo.", TODOS, "?estado="),
        ("POST", "/api/mantenciones", "Pedir un técnico (arreglo, reemplazo, retiro o mantención).", SOLICITAN, None),
        ("PATCH", "/api/mantenciones/{id}", "Aceptar, avisar «en camino/en el lugar», cerrar con informe o devolver.", "técnico, supervisor y administrador", None),
        ("GET", "/api/tecnicos", "Técnicos disponibles.", SOLICITAN, ""),
        ("GET", "/api/ubicaciones", "Dónde está cada dispositivo según su GPS.", TODOS, ""),
        ("GET", "/api/dispositivos/{id}/trayecto", "Recorrido de un dispositivo.", TODOS, None),
    ]),
    ("Cliente particular (compró un equipo)", [
        ("POST", "/api/auth/registro", "Crear la cuenta con el código del equipo y su clave de activación; inicia la sesión.", "público", None),
        ("POST", "/api/mis-dispositivos/vincular", "Activar otro equipo en la instalación propia (código + clave).", "cliente", None),
        ("POST", "/api/dispositivos/{id}/clave-activacion", "Generar la clave de activación de un equipo en venta (se muestra una sola vez).", "administrador", None),
    ]),
    ("Sesión y cuenta", [
        ("POST", "/api/auth/login", "Iniciar sesión (devuelve el token CSRF).", "público", None),
        ("GET", "/api/auth/me", "Datos de la sesión actual.", TODOS, ""),
        ("POST", "/api/auth/logout", "Cerrar sesión.", TODOS, None),
    ]),
    ("Catálogo (lectura según alcance; escritura solo administrador)", [
        ("GET", "/api/usuarios", "Usuarios.", ADMIN[0], ""),
        ("GET", "/api/instalaciones", "Instalaciones.", TODOS, ""),
        ("GET", "/api/cultivos", "Cultivos.", TODOS, ""),
        ("GET", "/api/dispositivos", "Dispositivos (uno por cultivo).", TODOS, ""),
        ("GET", "/api/sensores", "Sensores.", TODOS, ""),
        ("GET", "/api/tipos-sensor", "Tipos de sensor y unidades.", TODOS, ""),
        ("GET", "/api/reglas-alerta", "Reglas de alerta.", TODOS, ""),
        ("GET", "/api/parametros", "Parámetros del sistema.", TODOS, ""),
    ]),
    ("Solo administrador", [
        ("GET", "/api/auditoria", "Registro de auditoría.", ADMIN[0], ""),
        ("GET", "/api/auditoria/verificar", "Verifica que la cadena de auditoría no fue alterada.", ADMIN[0], ""),
        ("GET", "/api/simulacion", "Estado del simulador.", ADMIN[0], ""),
        ("POST", "/api/simulacion/accion", "Provocar o terminar una falla simulada.", ADMIN[0], None),
        ("POST", "/api/mediciones/manual", "Cargar una medición a mano.", ADMIN[0], None),
    ]),
    ("Dispositivos (ingesta firmada)", [
        ("POST", "/api/v1/ingesta", "Los equipos envían mediciones firmadas con HMAC-SHA256 (X-Device-Id, X-Timestamp, X-Nonce, X-Signature).", "dispositivo", None),
    ]),
]


def _proyecto(url):
    m = re.match(r"https?://([a-z0-9-]+?)(-default-rtdb)?\.", (url or "").strip().lower())
    return m.group(1) if m else None


@ruta(("GET",), roles=ADMIN)
def documentacion(request):
    modo = rtdb().modo
    proyecto = _proyecto(settings.AQ.get("FIREBASE_URL")) if modo == "firebase" else None
    t = _panel(request.usuario)["totales"]
    return JsonResponse({
        "estado": {"dispositivos": t["dispositivos"], "en_linea": t["en_linea"], "sin_comunicacion": t["sin_comunicacion"],
                   "mediciones_24h": t["mediciones_24h"], "alertas_abiertas": t["alertas_abiertas"]},
        "grupos": [{"grupo": g, "rutas": [{"metodo": m, "ruta": r, "para": p, "quien": q, "ejemplo": e} for m, r, p, q, e in rs]} for g, rs in CATALOGO],
        "firebase": {
            "modo": modo, "proyecto": proyecto,
            "consola": f"https://console.firebase.google.com/project/{proyecto}/database" if proyecto else None,
        },
        "nota": "La sesión y el token CSRF son los de tu usuario; las pruebas de esta pantalla son solo de lectura (GET).",
    })
