# Conexión del celular SOLO a través de Firebase (sin la misma WiFi)

## Cómo funciona (relé por Firebase)
El servidor Django corre en un computador sin dirección pública. Por eso el celular **ya no se conecta al computador**:
los dos hablan únicamente con Firebase Realtime Database.

```
celular ──PUT relay/sol/{id}──▶ Firebase ──(escucha en tiempo real)──▶ Django ejecuta la petición
celular ◀──lee relay/resp/{id}── Firebase ◀──────────── escribe la respuesta ──┘
```

1. La interfaz móvil (HTML/CSS/JS de `web/`) viene **dentro de la app Android** (se copia al compilar).
2. Cada petición de la app se escribe en `relay/sol/{id}` (id = hora + 128 bits aleatorios).
3. `python manage.py runserver` escucha ese nodo en tiempo real (`core/relay.py`), ejecuta la petición con toda la
   seguridad normal de Django (sesión, CSRF, roles, límites, auditoría) y deja la respuesta en `relay/resp/{id}`.
4. La app lee la respuesta y la borra. El servidor borra las respuestas no leídas a los 2 minutos y descarta peticiones de más de 90 s.

Resultado: el celular puede estar con **datos móviles o en cualquier WiFi**. Solo se necesita que el celular y el computador tengan internet.

## Requisitos
* `.env` con `AQ_FIREBASE_URL=https://TU-PROYECTO-default-rtdb.firebaseio.com` y `firebase-credenciales.json` (ver `docs/firebase.md`).
* **Reglas**: al arrancar, el servidor revisa las reglas del proyecto y, si no incluyen `relay`, publica solo
  `firebase/database.rules.json` (se ve en la consola de arranque). Si no tiene permiso, pégalas a mano en
  Firebase → Realtime Database → Reglas → Publicar. Para no publicarlas automáticamente: `AQ_PUBLICAR_REGLAS=0`.
* App Android: `app/src/main/res/values/strings.xml` → `firebase_db_url` debe ser la misma base.

## Seguridad del relé
* Desde internet solo se puede **dejar** una petición nueva (no leer ni pisar las de otros) y **leer una respuesta
  conociendo su id** aleatorio. Todo lo demás de la base sigue cerrado.
* El token de sesión viaja dentro de la petición (HTTPS hasta Firebase) y se borra al atenderla. Las rutas de ingesta de
  dispositivos no pasan por el relé.
* Límites de ritmo por celular (cada app tiene un identificador aleatorio de equipo).

## Probar desde un navegador
`http://localhost:8000/movil?rele=https://TU-PROYECTO-default-rtdb.firebaseio.com` fuerza el relé (solo acepta direcciones de Firebase o la base local).

## Si la app dice «El servidor AquaPlants no respondió a través de Firebase»
* El computador debe estar ejecutando `python manage.py runserver` y tener internet.
* La consola de arranque debe decir «Base de datos: Firebase Realtime Database» (no «MODO LOCAL»).
* Si dice «Firebase rechazó la petición (HTTP 401)»: faltan las reglas del relé (ver Requisitos).
