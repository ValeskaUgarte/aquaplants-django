"""Simulación integrada: dispositivos AquaBrain virtuales y monitor dentro del propio servidor.

`python manage.py runserver` es el único comando necesario: este módulo arranca, en hilos del mismo proceso,
  - los dispositivos simulados (los mismos de simulador/simulador.py) que envían mediciones FIRMADAS al
    endpoint real /api/v1/ingesta, exactamente como lo haría un dispositivo físico;
  - el monitor que marca dispositivos sin comunicación.
Se controla desde la web (Administración > Simulación) o desde código. Para pasar a dispositivos reales basta
con pausar o desactivar la simulación (AQ_SIMULACION=0): el resto del sistema no cambia.
"""
import importlib.util
import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

from django.conf import settings

log = logging.getLogger("aquaplants.simulacion")


_modulo = None


def _modulo_simulador():
    global _modulo
    if _modulo is None:
        ruta = Path(settings.BASE_DIR) / "simulador" / "simulador.py"
        spec = importlib.util.spec_from_file_location("aq_simulador", ruta)
        _modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_modulo)
    return _modulo


def _cargar_clase_dispositivo():
    return _modulo_simulador().DispositivoSimulado


def escenarios():
    """Fallas que se pueden provocar en la demostración: [{codigo, etiqueta}]."""
    return [{"codigo": k, "etiqueta": v["etiqueta"]} for k, v in _modulo_simulador().ESCENARIOS.items()]


class Simulacion:
    def __init__(self):
        self.disponible = False
        self.activa = False
        self.intervalo = 3.0
        self.opciones = {"anomalias": True, "datos_erroneos": True, "corte_cada_seg": 240, "corte_duracion_seg": 45}
        self.dispositivos = []
        self.ciclos = 0
        self._hilo = None
        self._lock = threading.Lock()

    def reparar(self, codigo_dispositivo):
        """Alguien dio la solución: el dispositivo simulado recupera la conexión y deja de producir valores anómalos."""
        with self._lock:
            for d in self.dispositivos:
                if d.id_dispositivo == codigo_dispositivo:
                    d.reparar()

    # ------------------------------------------------------------ arranque
    def iniciar(self, api: str, intervalo: float = 3.0, activa: bool = True):
        if self._hilo and self._hilo.is_alive():
            return
        archivo = Path(settings.AQ["DATA_DIR"]) / "dispositivos-demo.json"
        if not archivo.exists():
            log.warning("No hay %s: ejecuta seed_demo. Simulación no iniciada.", archivo)
            return
        Dispositivo = _cargar_clase_dispositivo()
        carpeta = Path(settings.AQ["DATA_DIR"]) / "simulador-local"
        self.api, self.intervalo, self.activa = api.rstrip("/"), intervalo, activa
        self.dispositivos = [Dispositivo(api=self.api, dir_local=carpeta, opciones=self.opciones, log=log.info, **d)
                             for d in json.loads(archivo.read_text(encoding="utf-8"))]
        self.disponible = True
        self._hilo = threading.Thread(target=self._bucle, name="simulacion", daemon=True)
        self._hilo.start()

    def _esperar_servidor(self):
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{self.api}/api/salud", timeout=2).read()
                return True
            except OSError:
                time.sleep(1)
        return False

    def _bucle(self):
        if not self._esperar_servidor():
            log.error("El servidor no respondió: simulación detenida.")
            return
        log.info("Simulación iniciada: %d dispositivo(s) -> %s cada %s s", len(self.dispositivos), self.api, self.intervalo)
        while True:
            inicio = time.time()
            if self.activa:
                with self._lock:
                    for d in self.dispositivos:
                        try:
                            d.ciclo()
                        except Exception:  # noqa: BLE001
                            log.exception("fallo en dispositivo simulado %s", d.id_dispositivo)
                    self.ciclos += 1
            time.sleep(max(0.2, self.intervalo - (time.time() - inicio)))

    # ------------------------------------------------------------ control (web)
    def estado(self):
        with self._lock:
            return {
                "disponible": self.disponible,
                "activa": self.activa,
                "intervalo_seg": self.intervalo,
                "opciones": {"anomalias": self.opciones["anomalias"], "datos_erroneos": self.opciones["datos_erroneos"],
                             "cortes_automaticos": bool(self.opciones["corte_cada_seg"])},
                "escenarios": escenarios() if self.disponible else [],
                "dispositivos": [{
                    "codigo": d.id_dispositivo, "sistema": d.id_sistema, "cultivo": d.cultivo, "red_caida": d.red_caida,
                    "en_cola": len(d.cola), "escenario": d.escenario_activo(), **d.estadisticas,
                } for d in self.dispositivos],
            }

    def accion(self, nombre: str, codigo: str | None = None, escenario: str | None = None):
        with self._lock:
            objetivo = [d for d in self.dispositivos if codigo in (None, "", d.id_dispositivo)]
            if nombre == "pausar":
                self.activa = False
            elif nombre == "reanudar":
                self.activa = True
            elif nombre == "anomalia":
                for d in objetivo:
                    d.provocar_anomalia()
            elif nombre == "corte":
                for d in objetivo:
                    d.provocar_corte(45)
            elif nombre == "escenario":
                for d in objetivo:
                    d.provocar_escenario(escenario)
            elif nombre == "reparar":
                for d in objetivo:
                    d.reparar()
            elif nombre == "alternar_anomalias":
                self.opciones["anomalias"] = not self.opciones["anomalias"]
            elif nombre == "alternar_datos_erroneos":
                self.opciones["datos_erroneos"] = not self.opciones["datos_erroneos"]
            elif nombre == "alternar_cortes":
                self.opciones["corte_cada_seg"] = 0 if self.opciones["corte_cada_seg"] else 240
            else:
                return False
        return True


simulacion = Simulacion()


def iniciar_monitor():
    """Monitor de dispositivos sin comunicación en un hilo (equivale a `manage.py monitor`)."""
    from core.monitor import ciclo

    from django.db import close_old_connections

    def bucle():
        while True:
            close_old_connections()
            ciclo()
            close_old_connections()
            time.sleep(settings.AQ["MONITOR_INTERVAL_SECONDS"])

    threading.Thread(target=bucle, name="monitor", daemon=True).start()
