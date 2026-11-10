"""Caché de 1,5 s para lecturas que muchos clientes piden a la vez (panel, alertas, última lectura).

Toda escritura que cambia esos datos (ingesta, monitor, reconocer alerta, carga manual) llama a `limpiar()`,
así las pantallas muestran los cambios al instante y no el valor anterior."""
import threading
import time

_datos, _lock = {}, threading.Lock()


def fresco(clave, fn, ttl=1.5):
    with _lock:
        c = _datos.get(clave)
        if c and c[0] > time.time():
            return c[1]
    valor = fn()
    with _lock:
        _datos[clave] = (time.time() + ttl, valor)
    return valor


def limpiar():
    with _lock:
        _datos.clear()
