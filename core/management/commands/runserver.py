"""`python manage.py runserver` deja TODO funcionando con un solo comando.

Al iniciar: conecta con la base (Firebase RTDB, o el modo local compatible si no hay credenciales), carga los datos
de demostración si está vacía, y arranca dentro del mismo proceso los dispositivos simulados y el monitor.
Se desactiva con AQ_SIMULACION=0 (por ejemplo cuando hay dispositivos reales).
"""
import os
import socket

from django.conf import settings
from django.core.management import call_command
from django.core.management.commands.runserver import Command as RunserverCommand


def _ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


PUERTO_CANDADO = 47613
_candado = None


def _unico_servidor() -> bool:
    """En Windows dos `runserver` pueden escuchar el mismo puerto sin error. Si eso pasa hay DOS simulaciones enviando datos de los
    mismos dispositivos (una con la falla provocada y otra sin ella) y las alertas se abren y se cierran solas cada pocos segundos.
    Un puerto tomado en modo exclusivo sirve de candado: solo un servidor AquaPlants por computador. Se libera al cerrar el proceso."""
    global _candado
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", PUERTO_CANDADO))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _candado = s
    return True


class Command(RunserverCommand):
    default_addr = "0.0.0.0"        # escucha en toda la red: así el celular puede conectarse al computador

    def inner_run(self, *args, **options):
        if not _unico_servidor():
            self.stderr.write("\nYa hay otro servidor AquaPlants corriendo en este computador (otra terminal o ventana).\n"
                              "Ciérralo con Ctrl+C antes de iniciar uno nuevo: dos servidores a la vez duplican la simulación\n"
                              "y las alertas se abren y se cierran solas.\n")
            self.stderr.flush()
            os._exit(1)                                      # corre en un hilo del autoreload: sys.exit no cerraría el proceso
        from core.rtdb import rtdb
        from core import conexion_nube
        otro = conexion_nube.otro_servidor_activo() if rtdb().modo == "firebase" else None
        simular = os.environ.get("AQ_SIMULACION", "0" if settings.AQ["PRODUCCION"] else "1") != "0"
        if otro and not os.environ.get("RENDER"):           # RENDER=true lo define Render en su servidor
            self.stderr.write(f"\nYa hay otro servidor AquaPlants encendido ({otro['equipo']}: {otro['url']}) usando la misma base.\n"
                              "Usa ese, o apágalo (en Render: Suspend) y espera 3 minutos antes de iniciar uno aquí: dos servidores\n"
                              "a la vez duplican la simulación y las alertas se abren y se cierran solas.\n")
            self.stderr.flush()
            os._exit(1)
        if otro and simular:                                # Render despierta mientras un computador ya simula: no duplicar
            self.stdout.write(f"[simulación] No se inicia: el servidor de {otro['equipo']} ya está simulando con la misma base.")
            simular = False
        aviso_nube = ""
        # En desarrollo la simulación arranca sola; en producción solo si se pide expresamente (AQ_SIMULACION=1, p. ej. la demo en Render).
        if simular:
            from core.monitor import ciclo  # noqa: F401
            from core.simulacion import iniciar_monitor, simulacion
            self.stdout.write(f"\nConectando con la base de datos ({'Firebase' if settings.AQ['FIREBASE_URL'] else 'modo local'})...")
            call_command("seed_demo")
            call_command("usuarios_demo")                     # asegura al segundo encargado de cultivo (con los 4 dispositivos)
            try:
                from core.management.commands.seed_demo import completar_sensores
                n = completar_sensores()                      # agrega sensores y alertas nuevos a una base ya sembrada (sin borrar nada)
                if n:
                    self.stdout.write(f"[sensores] Se agregaron {n} registros nuevos (agua, luz, N-P-K, pH, gases y sus alertas).")
            except Exception as e:                            # nunca impide que el servidor arranque
                self.stdout.write(f"[sensores] No se pudieron agregar los sensores nuevos: {e}")
            try:
                from core.management.commands.seed_demo import aplicar_referencias
                n = aplicar_referencias()                     # una sola vez: variables por cultivo y umbrales de las tablas de referencia
                if n:
                    self.stdout.write(f"[cultivos] Variables y umbrales de referencia aplicados ({n} cambios).")
            except Exception as e:
                self.stdout.write(f"[cultivos] No se pudieron aplicar las referencias por cultivo: {e}")
            try:
                from core.management.commands.seed_demo import aplicar_variables_informe
                n = aplicar_variables_informe()               # una sola vez: cada cultivo muestra solo las variables de su tabla del informe
                if n:
                    self.stdout.write(f"[cultivos] Variables del informe aplicadas ({n} cambios).")
            except Exception as e:
                self.stdout.write(f"[cultivos] No se pudieron aplicar las variables del informe: {e}")
            simulacion.iniciar(f"http://127.0.0.1:{self.port}", intervalo=float(os.environ.get("AQ_SIM_INTERVALO", "5")))
            iniciar_monitor()
        from core import acceso
        for linea in acceso.reparar_dispositivos() + acceso.asegurar_acceso():            # desbloquea cuentas y sincroniza la clave del .env
            self.stdout.write(f"[acceso] {linea}")
        aviso_relay = ""
        if settings.AQ["RELAY"]:
            from core import relay
            aviso_relay = relay.iniciar() or "Conexión del celular por Firebase activa."   # el celular habla con Firebase, no con este computador
        if not settings.AQ["PRODUCCION"] or settings.AQ["URL_PUBLICA"]:
            from core import conexion_nube, descubrimiento
            aviso_nube = conexion_nube.iniciar(int(self.port))      # latido: la app muestra si el servidor está encendido
            if os.environ.get("AQ_DESCUBRIMIENTO", "1") != "0":
                descubrimiento.iniciar(int(self.port))
            self.stdout.write(self.style.SUCCESS(
                f"\nBase de datos : {'Firebase Realtime Database  ' + settings.AQ['FIREBASE_URL'] if rtdb().modo == 'firebase' else 'MODO LOCAL (sin Firebase): configura AQ_FIREBASE_URL para usar la nube'}"
                f"\nWeb           : http://localhost:{self.port}"
                f"\nCelular       : se conecta SOLO a través de Firebase (datos móviles o cualquier WiFi; no necesita la misma red)."
                f"\n                {aviso_relay if rtdb().modo == 'firebase' else 'Sin Firebase (modo local): el celular no puede conectarse por la nube.'}"
                f"\n                (navegador del celular en la misma red: http://{_ip_local()}:{self.port}/movil)"
                f"\nClaves        : {'AQ_DEMO_PASSWORD (.env)' if acceso.clave_demo() else 'data/credenciales-demo.txt'}\n"))
        super().inner_run(*args, **options)
