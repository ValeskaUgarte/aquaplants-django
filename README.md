# AquaPlants / AquaBrain — Django + Firebase Realtime Database

Plataforma de monitoreo de cultivos aeropónicos. **Todo es simulación**, construida con la arquitectura de un sistema real.

* **Base de datos:** Firebase Realtime Database (plan gratuito). Sin SQL.
* **Backend:** Django (API, seguridad, reglas, alertas, monitor). Sin Node.
* **Web** (`/`): centro de monitoreo para escritorio.
* **Consola** (`/admin`): administración de todo el catálogo, al estilo del sitio de administración de Django.
* **Móvil** (`/movil`): operación en terreno, centrada en las alertas y en cada cultivo.
* **Simulación:** un dispositivo AquaBrain virtual **por cultivo**, controlable desde la web.

No necesita dominio, hosting ni servidor pago.

## Regla del proyecto: un dispositivo por cultivo
Cada cultivo se monitorea con **un** dispositivo, y en todas las pantallas se ve *qué cultivo* se está leyendo y *de qué
dispositivo* viene. El sistema lo exige (no deja asignar un segundo dispositivo a un cultivo). Los datos de demostración:

| Instalación | Cultivo | Dispositivo | Plantas | Niveles normales simulados (caudal · nutrientes · luz) |
| --- | --- | --- | --- | --- |
| Invernadero Norte | Lechuga | AQB-01 | 4 | 2,0 L/min · 700 ppm · 14 h |
| Invernadero Norte | Albahaca | AQB-02 | 3 | 2,4 L/min · 900 ppm · 16 h |
| Invernadero Sur | Tomate cherry | AQB-03 | 3 | 3,2 L/min · 1500 ppm · 14 h |
| Invernadero Sur | Frutilla | AQB-04 | 3 | 2,2 L/min · 1100 ppm · 12 h |

Cada cultivo tiene sus **propias reglas de alerta** (por ejemplo, 1,9 L/min es normal para la lechuga pero es caudal bajo
para el tomate). Los valores son de referencia pedagógica, no recomendaciones agronómicas.

## Ejecutar

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python manage.py runserver
```

Con ese único comando se conecta a la base, carga los datos de demostración si está vacía, y arranca los dispositivos
simulados, el monitor y el aviso de presencia para la app móvil. Abre http://localhost:8000. Las claves de los usuarios
quedan en `data/credenciales-demo.txt`.

### ¿Firebase o modo local?
* **Con Firebase:** sigue `docs/firebase.md` (crear el proyecto, copiar `.env.example` a `.env` y poner tu URL).
* **Sin configurar nada:** arranca en *modo local*, un servidor compatible con la API de Firebase dentro del proceso
  (guarda en `data/rtdb-local.json`). Es el mismo código; sirve para trabajar sin internet. La web indica qué base usa.

### Consola de administración (`/admin`)
Entrando como administrador (también hay un enlace «Consola Django» en el menú de la web) se gestiona todo: usuarios,
instalaciones, cultivos y plantas, dispositivos y sensores, tipos de sensor, reglas de alerta, parámetros, carga manual
y auditoría, además de consultar alertas y eventos. *Nota:* el sitio de administración integrado de Django necesita una
base SQL; como los datos viven en Firebase, esta consola es el equivalente construido para este sistema.

### App móvil: se conecta SOLO por Firebase (sin la misma WiFi)
La app Android trae la interfaz móvil dentro y envía cada petición a Firebase (`relay/sol`); este servidor la escucha en
tiempo real, la ejecuta con toda la seguridad de Django y deja la respuesta en Firebase (`relay/resp`). El celular puede
usar datos móviles o cualquier WiFi. Las reglas de Firebase necesarias se publican solas al arrancar. Ver `docs/conexion-movil.md`.

## Novedades de esta versión
* **Celular conectado solo por Firebase (relé):** ya no depende de estar en la misma red WiFi ni de la IP del computador (`core/relay.py`).
* **Se corrigió que todo quedara «sin comunicación»:** la cola local de los dispositivos simulados había crecido a ~36.000
  mediciones y cada envío a Firebase superaba el tiempo de espera, así que los dispositivos nunca volvían a «en línea».
  Ahora la cola tiene un máximo (2.000, se conservan las más recientes), los lotes son de 150 y el servidor solo consulta
  duplicados cuando realmente puede haberlos. Al volver la conexión, el dispositivo se pone al día en segundos.
* **Fallas demostrables (una alerta clara cada una):** falta y exceso de agua, falla de bomba, falta/exceso de nutrientes,
  falta de nitrógeno, falta/exceso de luz solar, gas amoníaco, CO₂ alto, pH ácido/alcalino, temperatura alta y corte de
  comunicación. Se provocan por cultivo (web → Simulación, o app → Gestión → Simular fallas) y duran hasta que alguien da la solución.
* **Confirmar ahora sí funciona:** al confirmar se revisa el valor ACTUAL del sensor (o si el dispositivo volvió a comunicarse);
  si todavía no está normal se explica por qué. Los mensajes de error/éxito quedan visibles (ya no los borra el refresco).
* **Estado en cada dispositivo** (web y app): fallas abiertas con quién las atiende, y «✔ Solucionado: … · solución de … · confirmó …».
* **App móvil sin saltos a la web:** «Más/Gestión» abre pantallas propias de la app (bitácora, comparación, dispositivos, simulación,
  usuarios, reglas, instalaciones y cultivos, parámetros, equipos, auditoría, informes). El botón atrás de Android vuelve dentro de la app.
* **Alertas con sentido para el cultivo:** además de caudal, nutrientes y exceso de luz, ahora hay sensores y alertas de nivel de agua bajo,
  falta y exceso de luz solar, pocas horas de luz, falta de nitrógeno (N), fósforo (P) y potasio (K), pH ácido/alcalino y gases contaminantes
  (CO₂ y amoníaco). Una base ya sembrada los recibe sola al arrancar `runserver` (sin borrar nada).
* **Supervisor operativo:** puede reconocer, revisar y dar solución a una alerta (o registrar que no pudo solucionarla) cuando el encargado no está.
  Si el supervisor da la solución, la alerta queda resuelta de inmediato.
* **Bitácora de alertas:** cada acción (reconoció, dio solución, no pudo solucionar, confirmó, rechazó) queda con quién, su rol, cuándo y la nota.
* **Estado en cada dispositivo:** la tarjeta del cultivo (web y móvil) muestra «Alerta activa», «En revisión», «Falta confirmar» o
  «✔ Problema solucionado … confirmó …».
* **Dar la solución ya no se bloquea:** se puede registrar «sí, quedó solucionado» aunque el valor aún no se normalice; el supervisor solo puede
  confirmarla cuando el dispositivo informa el valor normal. En la simulación, al dar la solución el dispositivo se recupera solo.
* **App móvil, pestaña «Más»** (supervisor y administrador): seguimiento de alertas, comparación, informes, administración y simulación.
* **Alertas:** se corrigió la creación de alertas (la función estaba truncada y dejó de generarlas) y se limpian claves de alerta bloqueadas.
* **Notificaciones:** campana en web y móvil, aviso del navegador/sistema y push en el celular. Encargados, supervisores y administradores
  reciben las alertas y cada comentario de revisión o confirmación.
* **Segundo encargado:** `encargado2@aquaplants.demo` ve los 4 dispositivos (`python manage.py usuarios_demo`; la clave queda en `data/credenciales-demo.txt`).
* **Informes (Administrador):** Excel o CSV por fecha, instalación, cultivo, variable y estado de alertas (`/admin` → Informes, o Informes en la web).
* **Seguimiento de alertas:** quién reconoció, qué comentó el encargado y qué respondió el supervisor; pendientes y resueltas.
* **Vinculación de equipos:** cada ingreso vincula el celular/navegador a la cuenta; se ve en *Mi cuenta* y el administrador puede revocar (consola → Equipos vinculados).
* **Logo de AquaPlants** en login, menús, favicon e íconos de la app Android.

## Qué se puede mostrar
* Panel por instalación → cultivo → dispositivo, con las lecturas en vivo (cada 5 s).
* Estado actual por cultivo, histórico con los umbrales de cada regla dibujados, comparación entre cultivos o instalaciones.
* Alertas por umbral y por dispositivo sin comunicación (`activa → reconocida → resuelta`, con resolución automática).
* Eventos: sin comunicación, reconexión, sincronización diferida, datos rechazados.
* Panel **Simulación** (administrador): pausar, provocar anomalía (dura ~30 s para poder verla y reconocerla), cortar la red
  (los dispositivos guardan en cola local y sincronizan al volver), datos erróneos. Cada acción queda en la auditoría.
* Tres roles: administrador, supervisor (todo en lectura + comparación), encargado de cultivo (solo sus instalaciones, reconoce alertas).
* Seguridad: contraseñas de 12+ caracteres, 2FA (TOTP), bloqueo progresivo, sesiones revocables, secretos cifrados,
  firma HMAC de dispositivos, auditoría encadenada verificable.
* En la consola de Firebase se ve la base cambiando en vivo.

## Pruebas automáticas
```bash
python manage.py test core
```
67 pruebas: relé por Firebase, fallas simuladas, flujo de «sin comunicación», autenticación, 2FA, política de contraseñas, roles y alcance, regla de un dispositivo por cultivo, ingesta
firmada (firma, nonce, rechazos, duplicados, diferidos, fuera de orden), alertas con cultivo y umbrales propios,
monitor y eventos, histórico, comparación, carga manual, auditoría, simulación, interfaces, descubrimiento para la app y
compatibilidad con la API REST de Firebase. Corren contra el servidor local compatible; la conexión con la nube se valida con tu proyecto.

## Otros comandos
* `python manage.py reiniciar_datos` borra la base para sembrar de nuevo.
* `AQ_SIMULACION=0 python manage.py runserver` inicia sin simulación (por ejemplo, con dispositivos reales).
* `python manage.py monitor` y `python simulador/simulador.py` ejecutan esas piezas por separado.

## Pasar de la simulación a la realidad
Ver `docs/arquitectura.md`: se crea el cultivo y su dispositivo desde la consola, un ESP32/Arduino envía al mismo endpoint
firmado (`docs/api.md`) y se pausa la simulación. La web, la app y la base no cambian.

## Estructura
```
config/        configuración Django
core/          rtdb.py (cliente Firebase), emulador.py (modo local), repo, seguridad, ingesta, alertas, monitor,
               simulacion, descubrimiento, vistas/ (API), management/commands, tests/
web/           index.html (web), movil.html (móvil), admin.html (consola) y static/ en HTML/CSS/JS plano
simulador/     dispositivos AquaBrain virtuales (solo biblioteca estándar)
firebase/      reglas de seguridad para pegar en la consola
docs/          firebase.md, arquitectura.md, api.md
```

## Limitaciones (declaradas)
Datos y umbrales simulados, de referencia pedagógica. Diseñado para un solo servidor Django. Con la nube, cada petición
viaja a Firebase por internet, por lo que es algo más lenta que en modo local. «Mediciones 24 h» del panel se calcula con
contadores por hora (aproximado a la hora). Sin parámetro de retención no se borran históricos: con la simulación
encendida la base crece unos 15 MB por día; fija `retencion_historicos_dias` en Administración → Parámetros si la usas
mucho tiempo con Firebase gratuito.
