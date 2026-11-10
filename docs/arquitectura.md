# Arquitectura

```
 Dispositivos AquaBrain ──HMAC──▶  Django (API, seguridad, reglas, alertas, monitor) ──▶ Firebase Realtime Database
 (simulados hoy, ESP32 mañana)         ▲                                                    (única base de datos)
                                       │ JSON (sesión + CSRF)
                       ┌───────────────┴───────────────┐
                       ▼                               ▼
        WEB  /  (monitoreo, escritorio)     MÓVIL  /movil  (operación en terreno)
```

* **Firebase RTDB** guarda todo: usuarios, sesiones, catálogo, mediciones, alertas y auditoría. No hay base SQL.
* **Django** es la única puerta de entrada: autentica, aplica roles, valida la firma de los dispositivos, evalúa las reglas
  y escribe en Firebase. Las interfaces nunca hablan directo con Firebase (las reglas lo prohíben).
* **Web** (`/`): centro de monitoreo para escritorio. Vista global con KPIs, tarjetas por instalación, gráficos
  históricos (15 min a 24 h), tablas de alertas y dispositivos, auditoría y administración. Se actualiza cada 3 s.
* **Móvil** (`/movil`): operación en terreno, centrada en *"¿qué necesita mi atención ahora?"*: estado del día,
  alertas con botón de reconocer, aviso con vibración cuando entra una alerta nueva, detalle breve por cultivo.
  La app Android (WebView) recibe esta interfaz automáticamente; desde el navegador del celular se abre `/movil`.
* **Sin Node**: ambas interfaces son HTML/CSS/JS plano servido por Django (carpeta `web/`), con gráficos en SVG propio.

## Modelo de datos en Firebase (un dispositivo por cultivo)

| Ruta | Contenido |
| --- | --- |
| `usuarios/{id}`, `indice_email/{email}` | correo, nombre, rol, hash scrypt, instalaciones asignadas, intentos fallidos, 2FA |
| `sesiones/{sha256(token)}`, `sesiones_usuario/{id}` | sesión (token CSRF, expiración, IP); índice para revocarlas |
| `instalaciones/{id}` | código, nombre, descripción |
| `cultivos/{id}`, `plantas/{id}` | cultivos de cada instalación y sus plantas |
| `dispositivos/{id}` | **cultivo que monitorea (uno por cultivo)**, instalación, secreto **cifrado**, estado, última comunicación |
| `tipos_sensor/{codigo}`, `sensores/{id}` | tipos con límites de validación; sensores de cada dispositivo (miden el cultivo de su dispositivo) |
| `reglas/{id}` | reglas de alerta por sensor (propias de cada cultivo) o por tipo; operador y umbral |
| `parametros/{clave}` | minutos sin comunicación, retención de históricos |
| `mediciones/{sensor}/{timestamp}` | historial (clave = milisegundos con 13 dígitos, ordenable) |
| `ultima/{sensor}`, `contadores/{instalacion}/{hora}` | última lectura; contadores atómicos por hora |
| `alertas/{id}`, `alertas_abiertas/{clave}` | alertas y su índice anti-duplicados |
| `eventos/{id}`, `auditoria/{id}` | eventos operativos; registros encadenados con hash |

## Roles
* **Administrador**: todo (usuarios, estructura, dispositivos, reglas, parámetros, carga manual, auditoría, simulación).
* **Supervisor**: ve todas las instalaciones y la comparación; no reconoce alertas ni administra.
* **Encargado de cultivo**: solo las instalaciones asignadas; reconoce alertas.

## Alertas
`activa → reconocida → resuelta`. Una alerta se abre cuando una medición cumple una regla y se **resuelve sola** cuando
deja de cumplirla (o, en las de *sin comunicación*, cuando el dispositivo vuelve). No se duplica mientras siga abierta.
Un dato que llega fuera de orden queda en el histórico pero no altera las alertas. Cada alerta indica cultivo y dispositivo.

## Seguridad
Contraseñas con scrypt; cookie `HttpOnly` + token CSRF; bloqueo tras 5 intentos fallidos; límite de peticiones; roles
validados en el servidor; secretos de dispositivos cifrados (Fernet) y nunca devueltos salvo al crearlos; firma HMAC-SHA256
con ventana de tiempo y nonce anti-repetición; auditoría encadenada que detecta alteraciones; cabeceras HTTP
de seguridad (CSP sin scripts ni estilos en línea).

## Simulación hoy, realidad mañana
Los dispositivos virtuales (`simulador/simulador.py`, corren dentro del servidor) usan el mismo protocolo que un
dispositivo físico: lotes JSON firmados a `/api/v1/ingesta`, cola local persistida ante cortes de red, reintentos con
espera exponencial y reenvío marcado como `diferido`. Para pasar a hardware real: crear el cultivo y registrar su dispositivo en
**Administración** (entrega su secreto una sola vez), programar el ESP32/Arduino con el protocolo de `docs/api.md` y
pausar la simulación (o `AQ_SIMULACION=0`). La web, la app y la base de datos no cambian.

## Limitaciones declaradas
* Todos los datos, umbrales y dispositivos son simulados y de referencia pedagógica.
* Diseñado para un solo servidor Django (cachés y control de repetición en memoria). Varios servidores exigirían
  mover esos estados a la base de datos.
* Cada petición consulta Firebase a través de internet: con la nube la latencia es mayor que en modo local.
* Las pruebas automáticas se ejecutan contra un servidor local compatible con la API REST de Firebase; la conexión
  con la nube se valida siguiendo `docs/firebase.md` con tu propio proyecto.
