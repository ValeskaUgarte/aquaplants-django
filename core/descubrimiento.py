"""Descubrimiento automático del servidor en la red local.

La app móvil NO pide ninguna dirección: envía por difusión UDP el mensaje «AQUAPLANTS?» y este hilo responde
«AQUAPLANTS <puerto>». Así la app encuentra el computador sin que nadie escriba una IP.
"""
import logging
import socket
import threading

log = logging.getLogger("aquaplants.descubrimiento")
PUERTO_UDP = 8765


def iniciar(puerto_http: int, puerto_udp: int = PUERTO_UDP) -> int:
    """Devuelve el puerto UDP en que escucha (0 si no pudo iniciar)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", puerto_udp))
    except OSError as e:
        log.warning("Descubrimiento automático desactivado (puerto UDP %s ocupado): %s", puerto_udp, e)
        sock.close()
        return 0

    def bucle():
        while True:
            try:
                datos, origen = sock.recvfrom(256)
                if datos.strip() == b"AQUAPLANTS?":
                    sock.sendto(f"AQUAPLANTS {puerto_http}".encode(), origen)
            except OSError:
                return

    threading.Thread(target=bucle, name="descubrimiento", daemon=True).start()
    return sock.getsockname()[1]
