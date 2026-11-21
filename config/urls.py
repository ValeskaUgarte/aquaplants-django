from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve

from core.vistas import auth, catalogo, estadisticas, informes, ingesta, documentacion, mantencion, notificaciones, operacion, simulacion, ubicacion, vinculacion, web
from core.http import (Campo, ErrorApi, booleano, conflicto, exigir_acceso_instalacion, fecha_iso, instalaciones_visibles,
                       invalido, ip_cliente, no_encontrado, numero, id_valor, ruta, sin_contenido, texto, validar)

urlpatterns = [
    path("api/salud", web.salud),
    path("api/v1/ingesta", ingesta.ingesta),                     # dispositivos: firma HMAC, sin cookies

    # Autenticación
    path("api/auth/login", auth.login),
    path("api/auth/me", auth.me),
    path("api/auth/logout", auth.logout),
    path("api/auth/password", auth.cambiar_password),
    path("api/auth/2fa/iniciar", auth.dosfa_iniciar),
    path("api/auth/2fa/activar", auth.dosfa_activar),
    path("api/auth/2fa/desactivar", auth.dosfa_desactivar),

    # Catálogo
    path("api/usuarios", catalogo.usuarios),
    path("api/usuarios/<str:pk>", catalogo.usuario_detalle),
    path("api/instalaciones", catalogo.instalaciones),
    path("api/instalaciones/<str:pk>", catalogo.instalacion_detalle),
    path("api/cultivos", catalogo.cultivos),
    path("api/cultivos/<str:pk>", catalogo.cultivo_detalle),
    path("api/plantas", catalogo.plantas),
    path("api/plantas/<str:pk>", catalogo.planta_detalle),
    path("api/tipos-sensor", catalogo.tipos_sensor),
    path("api/tipos-sensor/<str:pk>", catalogo.tipo_sensor_detalle),
    path("api/dispositivos", catalogo.dispositivos),
    path("api/dispositivos/<str:pk>", catalogo.dispositivo_detalle),
    path("api/dispositivos/<str:pk>/rotar-secreto", catalogo.dispositivo_rotar_secreto),
    path("api/sensores", catalogo.sensores),
    path("api/sensores/<str:pk>", catalogo.sensor_detalle),
    path("api/reglas-alerta", catalogo.reglas),
    path("api/reglas-alerta/<str:pk>", catalogo.regla_detalle),
    path("api/parametros", catalogo.parametros),
    path("api/parametros/<str:clave>", catalogo.parametro_detalle),

    # Operación
    path("api/estado-actual", operacion.estado_actual),
    path("api/mediciones", operacion.mediciones),
    path("api/mediciones/manual", operacion.medicion_manual),
    path("api/alertas", operacion.alertas),
    path("api/alertas/<str:pk>/reconocer", operacion.reconocer_alerta),
    path("api/alertas/<str:pk>/revisar", operacion.revisar_alerta),
    path("api/alertas/<str:pk>/verificar", operacion.verificar_alerta),
    path("api/alertas/<str:pk>/derivar", mantencion.derivar_alerta),
    path("api/mantenciones", mantencion.mantenciones),
    path("api/tecnicos", mantencion.tecnicos),
    path("api/ubicaciones", ubicacion.ubicaciones),
    path("api/config-mapa", ubicacion.config_mapa),
    path("api/dispositivos/<str:pk>/trayecto", ubicacion.trayecto),
    path("api/dispositivos/<str:pk>/ubicacion", ubicacion.ubicacion_manual),
    path("api/estadisticas", estadisticas.estadisticas),
    path("api/mantenciones/<str:pk>", mantencion.mantencion_detalle),
    path("api/eventos", operacion.eventos),
    path("api/panel", operacion.panel),
    path("api/movil/resumen", operacion.resumen_movil),
    path("api/comparacion", operacion.comparacion),
    path("api/documentacion", documentacion.documentacion),
    path("api/auditoria", operacion.auditoria),
    path("api/auditoria/verificar", operacion.verificar_auditoria),

    # Notificaciones, seguimiento e informes
    path("api/notificaciones", notificaciones.listar),
    path("api/notificaciones/leer", notificaciones.leer),
    path("api/seguimiento-alertas", informes.seguimiento),
    path("api/informes", informes.descargar),
    path("api/informes/trabajadores", informes.trabajadores),

    # Vinculación de la cuenta con el equipo (celular / navegador)
    path("api/vinculacion/registrar", vinculacion.registrar),
    path("api/vinculacion/mis-equipos", vinculacion.mis_equipos),
    path("api/vinculacion/<str:pk>/desvincular", vinculacion.desvincular),
    path("api/equipos", vinculacion.todos),
    path("api/equipos/<str:pk>/revocar", vinculacion.revocar),
    path("api/equipos/<str:pk>/reactivar", vinculacion.reactivar),

    # Simulación (solo administrador)
    path("api/simulacion", simulacion.estado),
    path("api/simulacion/accion", simulacion.accion),

    re_path(r"^api/.*$", web.no_encontrado_api),

    # Interfaces: web (monitoreo) y móvil (operación en terreno)
    path("", web.inicio),
    path("movil", web.movil),
    path("admin", web.consola),
    path("admin/", web.consola),
    re_path(r"^static/(?P<path>.+)$", serve, {"document_root": settings.WEB_DIR / "static"}),
]
