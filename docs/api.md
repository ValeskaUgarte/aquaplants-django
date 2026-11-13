# API

Todas las rutas devuelven JSON; los errores tienen la forma `{ "error": { "codigo", "mensaje", "detalles"? } }`.
Las rutas de usuario requieren sesión (cookie `aq_sid`) y, en métodos que modifican, la cabecera `X-CSRF-Token`
(se entrega como `csrfToken` en `login` y `me`).

## Dispositivos
`POST /api/v1/ingesta`. Cabeceras: `X-Device-Id`, `X-Timestamp` (segundos Unix, 10 dígitos), `X-Nonce` (16–64 caracteres, único),
`X-Signature` = HMAC-SHA256 hexadecimal con el secreto del dispositivo sobre `"{timestamp}.{nonce}.{cuerpo exacto}"`.

```json
{ "id_sistema": "INV-NORTE", "id_dispositivo": "AQB-01", "calidad_enlace": 87,
  "mediciones": [ { "id_sensor": "CAU1", "tipo_variable": "caudal", "valor": 2.05, "unidad": "L/min",
                    "timestamp": "2026-11-06T14:05:00-03:00", "estado_conexion": "conectado" } ] }
```
Posición del GPS (opcional, en cualquier lote, también en un latido): `"gps": { "lat": -33.4489, "lng": -70.6693, "precision": 4.5, "altitud": 520 }`
(`precision` y `altitud` en metros, opcionales). La web muestra la última posición, su recorrido (se guarda cuando el equipo se mueve ≥ 30 m o cada 30 min)
y deja un evento si se mueve ≥ 100 m. Coordenadas fuera de rango → 400.

`id_sistema` es el código de la instalación; `id_dispositivo`, el del dispositivo (que pertenece a un cultivo). `estado_conexion`:
`conectado` (tiempo real) o `diferido` (guardada durante un corte y reenviada). Un lote vacío es un latido.
Respuesta 200: `{ aceptadas, duplicadas, rechazadas:[{indice, motivo}], alertas_generadas:[{id_sensor, regla, valor}] }`.
Códigos: 400 formato o lote > 500, 401 firma/timestamp/nonce inválidos, 403 identidad o dispositivo inactivo, 429 límite.
Motivos de rechazo: `formato_invalido`, `sensor_desconocido_o_inactivo`, `tipo_variable_no_coincide`, `unidad_no_coincide`,
`valor_fuera_de_limites_de_validacion`, `timestamp_en_el_futuro`.

## Servicio técnico, ubicación y estadísticas
Rol `tecnico`: no ve cultivos ni alertas, solo solicitudes. `POST /api/alertas/{id}/derivar` y `POST /api/mantenciones` (encargado, supervisor, administrador: `tipo`, `accion` = arreglo|reemplazo|retiro|mantencion, `nota`,
`tecnico_id`?) crean una solicitud; va al técnico asignado a la instalación (`tecnico_id` en la instalación) o a la bolsa de todos los técnicos. `GET /api/mantenciones?estado=` ·
`PATCH /api/mantenciones/{id}` (técnico: `en_curso`, `completada` + `resolucion`, `devuelta`; supervisor y administrador: además asignar con `tecnico_id` o `cancelada`) · `GET /api/tecnicos`.
Ubicación: `GET /api/ubicaciones` · `GET /api/dispositivos/{id}/trayecto` · `PATCH /api/dispositivos/{id}/ubicacion` (corrección manual, supervisor y administrador) ·
`GET /api/config-mapa` (clave pública de Google Maps, parámetro `google_maps_api_key`). `GET /api/estadisticas?dias=7..180`. Usuarios: `DELETE /api/usuarios/{id}` elimina la cuenta
(se conserva el nombre en el historial); `PATCH` acepta `dispositivos` (a cargo) y `suspension_motivo`.

## Sesión y cuenta
`POST /api/auth/login` (`email`, `password`, `codigo_totp`?) · `GET /api/auth/me` · `POST /api/auth/logout` · `POST /api/auth/password` ·
`POST /api/auth/2fa/iniciar|activar|desactivar`.

## Consulta (acotada a las instalaciones del usuario)
`GET /api/panel` · `GET /api/estado-actual?instalacion_id=` · `GET /api/mediciones?sensor_id=&desde=&hasta=&agregacion=` ·
`GET /api/alertas?estado=abiertas|activa|reconocida|resuelta&instalacion_id=` · `POST /api/alertas/{id}/reconocer` (encargado y administrador) ·
`GET /api/eventos` · `GET /api/comparacion?tipo_variable=&agrupar=cultivo|instalacion` (supervisor y administrador) · `GET /api/salud` (público).

## Catálogo (lectura según alcance; escritura solo administrador)
`/api/usuarios` · `/api/instalaciones` · `/api/cultivos` · `/api/plantas` · `/api/tipos-sensor` · `/api/dispositivos` (+ `POST /{id}/rotar-secreto`) ·
`/api/sensores` · `/api/reglas-alerta` · `/api/parametros` (+ `PUT /{clave}`). Crear un dispositivo exige `cultivo_id`: un dispositivo por cultivo.

## Solo administrador
`POST /api/mediciones/manual` · `GET /api/auditoria` · `GET /api/auditoria/verificar` · `GET /api/simulacion` · `POST /api/simulacion/accion`
(`pausar`, `reanudar`, `anomalia`, `corte`, `alternar_anomalias`, `alternar_datos_erroneos`, `alternar_cortes`).
