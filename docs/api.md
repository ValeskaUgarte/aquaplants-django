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
`id_sistema` es el código de la instalación; `id_dispositivo`, el del dispositivo (que pertenece a un cultivo). `estado_conexion`:
`conectado` (tiempo real) o `diferido` (guardada durante un corte y reenviada). Un lote vacío es un latido.
Respuesta 200: `{ aceptadas, duplicadas, rechazadas:[{indice, motivo}], alertas_generadas:[{id_sensor, regla, valor}] }`.
Códigos: 400 formato o lote > 500, 401 firma/timestamp/nonce inválidos, 403 identidad o dispositivo inactivo, 429 límite.
Motivos de rechazo: `formato_invalido`, `sensor_desconocido_o_inactivo`, `tipo_variable_no_coincide`, `unidad_no_coincide`,
`valor_fuera_de_limites_de_validacion`, `timestamp_en_el_futuro`.

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
