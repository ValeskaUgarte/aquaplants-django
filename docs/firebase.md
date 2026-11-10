# Conectar el proyecto a Firebase Realtime Database

Firebase es la base de datos del proyecto. El plan gratuito (Spark) alcanza para la demostración: no hace falta
dominio, hosting ni tarjeta. Sin estos pasos el proyecto funciona igual en **modo local** (mismo código, sin internet).
La consola de Firebase cambia de aspecto de vez en cuando; los nombres de las secciones son los de hoy.

## 1. Crear el proyecto y la base
1. Entra a https://console.firebase.google.com con tu cuenta de Google → **Crear un proyecto** (puedes desactivar Analytics).
2. Menú **Compilación (Build) → Realtime Database → Crear base de datos**. Elige la ubicación y **modo bloqueado**.
3. En la pestaña **Datos** copia la dirección que aparece arriba, parecida a
   `https://mi-proyecto-default-rtdb.firebaseio.com` (en otras regiones termina en `firebasedatabase.app`).

## 2. Reglas de seguridad
Pestaña **Reglas** → pega el contenido de `firebase/database.rules.json` → **Publicar**.
Esas reglas impiden que cualquiera lea o escriba directamente desde internet. Solo Django accede, con una cuenta de
servicio (que no depende de las reglas). Por eso los usuarios, sesiones y secretos no quedan expuestos.

## 3. Credenciales de la cuenta de servicio
**Configuración del proyecto (engranaje) → Cuentas de servicio → Generar nueva clave privada**. Guarda el archivo `.json`
como `firebase-credenciales.json` en la carpeta del proyecto (junto a `manage.py`).
No lo compartas ni lo subas a GitHub (ya está en `.gitignore`).

## 4. Configurar y ejecutar
1. Copia `.env.example` como `.env` y escribe tu dirección en `AQ_FIREBASE_URL=`.
2. `pip install -r requirements.txt`
3. `python manage.py runserver 0.0.0.0:8000`

La primera vez carga los datos de demostración en Firebase (tarda unos segundos). En la consola de Firebase →
**Datos** verás aparecer en vivo los nodos `estado`, `mediciones`, `alertas`, `dispositivos`, etc.
La consola de arranque indica `Base de datos: Firebase Realtime Database` cuando la conexión funciona.

## Cuidar la cuota gratuita
Cada dispositivo simulado (uno por cultivo) escribe una vez por ciclo. Si no estás mostrando el sistema, detén el servidor o
pausa la simulación (menú **Simulación**). Para escribir menos: `AQ_SIM_INTERVALO=10` en `.env`. Para que el sistema borre
solo los históricos viejos, fija `retencion_historicos_dias` en **Administración → Parámetros** (por defecto 0 = no borra).
Revisa en la consola de Firebase el uso y los límites vigentes.

## Volver a empezar
`python manage.py reiniciar_datos` borra todo el contenido de la base (¡toda!, no solo este proyecto) y la siguiente
ejecución de `runserver` vuelve a sembrar. Para volver al modo local, borra o renombra `.env`.

## Problemas frecuentes
* **"Firebase rechazó las credenciales…"**: revisa que `firebase-credenciales.json` sea de ESE proyecto y que `AQ_FIREBASE_URL` esté bien escrita (sin espacios ni `/` final).
* **"Permission denied"**: la cuenta de servicio no pertenece al proyecto de la URL.
* **La web muestra "BD: modo local"**: no se leyó el `.env` (debe estar junto a `manage.py` y llamarse exactamente `.env`).
