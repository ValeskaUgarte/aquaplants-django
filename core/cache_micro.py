"""Caché de 5 s para lecturas que muchos clientes piden a la vez (panel, alertas, última lectura).

Toda escritura que cambia esos datos (monitor, reconocer alerta, carga manual) llama a `limpiar()`, así las pantallas muestran
los cambios al instante y no el valor anterior. La ingesta, que ocurre varias veces por segundo, no borra la caché: actualiza en
memoria la última lectura (`parchar`) y solo limpia todo cuando cambió alguna alerta."""
import threading
import time

_datos, _lock = {}, threading.Lock()


def fresco(clave, fn, ttl=5):
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


def parchar(clave, fn):
    """Modifica un valor ya en caché sin consultar a Firebase (fn recibe el valor y devuelve el nuevo)."""
    with _lock:
        c = _datos.get(clave)
        if c and c[0] > time.time():
            _datos[clave] = (c[0], fn(c[1]))
