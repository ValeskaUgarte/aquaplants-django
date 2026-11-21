"""Dispositivo AquaBrain simulado (Python, solo biblioteca estándar: no requiere Node ni pip).

Comportamiento igual al simulador original en Node:
genera mediciones con timestamp;
firma cada envío (HMAC-SHA256 sobre "timestamp.nonce.cuerpo") y lo envía a la API;
ante pérdida de conectividad guarda las mediciones en una cola LOCAL persistida en disco;
reintenta con espera exponencial y, al volver la red, sincroniza marcándolas como "diferido".

Uso:
  python simulador.py --config ../data/dispositivos-demo.json --api http://127.0.0.1:8000 \
      --intervalo 5 --anomalias --datos-erroneos --corte-cada 240 --corte-duracion 45
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import random
import zlib
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = {"caudal": 2.5, "consumo_hidrico": 0.4, "nutrientes": 800, "fotoperiodo": 16, "parametro_cultivo": 6,
        "nivel_agua": 80, "luz_intensidad": 24, "nitrogeno": 170, "fosforo": 55, "potasio": 240, "ph": 6.0, "co2": 750, "amoniaco": 2.0,
        "temperatura": 21.0, "humedad": 65, "temperatura_ambiente": 20.0}

# Fallas demostrables del cultivo: cada escenario cambia los sensores afectados (valor = f(nivel normal del cultivo)).
# Cada una dispara UNA alerta clara (los sensores relacionados se mueven, pero sin salir de su rango).
# Duran hasta que alguien da la solución (o hasta que se cumple su tiempo), como pasaría con una falla real.
ESCENARIOS = {
    "agua_emergencia":   {"etiqueta": "Emergencia de agua (estanque casi vacío, ≤5 %)", "cambios": {"nivel_agua": lambda b: 4, "caudal": lambda b: b * 0.75}},
    "falta_agua":        {"etiqueta": "Falta de agua (estanque bajo)", "cambios": {"nivel_agua": lambda b: 18, "caudal": lambda b: b * 0.75}},
    "exceso_agua":       {"etiqueta": "Exceso de agua (desborde)", "cambios": {"nivel_agua": lambda b: 99, "consumo_hidrico": lambda b: b * 2.4}},
    "falla_bomba":       {"etiqueta": "Falla de bomba (caudal bajo)", "cambios": {"caudal": lambda b: b * 0.3}},
    "falta_nutrientes":  {"etiqueta": "Falta de nutrientes", "cambios": {"nutrientes": lambda b: b * 0.55, "nitrogeno": lambda b: b * 0.8,
                                                                          "fosforo": lambda b: b * 0.8, "potasio": lambda b: b * 0.8}},
    "falta_nitrogeno":   {"etiqueta": "Falta de nitrógeno (N)", "cambios": {"nitrogeno": lambda b: b * 0.45}},
    "exceso_nutrientes": {"etiqueta": "Exceso de nutrientes", "cambios": {"nutrientes": lambda b: b * 1.5}},
    "falta_luz":         {"etiqueta": "Falta de luz solar", "cambios": {"luz_intensidad": lambda b: b * 0.35, "fotoperiodo": lambda b: b - 2}},
    "exceso_luz":        {"etiqueta": "Exceso de luz solar", "cambios": {"luz_intensidad": lambda b: b * 1.8, "fotoperiodo": lambda b: b + 2}},
    "gas_amoniaco":      {"etiqueta": "Gas dañino: amoníaco (NH₃)", "cambios": {"amoniaco": lambda b: b * 8}},
    "gas_co2":           {"etiqueta": "Gas dañino: CO₂ alto", "cambios": {"co2": lambda b: b * 2.2}},
    "ph_acido":          {"etiqueta": "pH demasiado ácido", "cambios": {"ph": lambda b: b - 1.6}},
    "ph_alcalino":       {"etiqueta": "pH demasiado alcalino", "cambios": {"ph": lambda b: b + 1.6}},
    "calor":             {"etiqueta": "Temperatura de la solución alta", "cambios": {"temperatura": lambda b: 31}},
    "humedad_alta":      {"etiqueta": "Humedad ambiental alta (riesgo de hongos)", "cambios": {"humedad": lambda b: 92}},
    "humedad_baja":      {"etiqueta": "Humedad ambiental baja (ambiente seco)", "cambios": {"humedad": lambda b: 30}},
    "helada":            {"etiqueta": "Riesgo de helada (temperatura ambiente baja)", "cambios": {"temperatura_ambiente": lambda b: 2}},
    "sin_comunicacion":  {"etiqueta": "Dispositivo sin comunicación (corte prolongado)", "cambios": {}},
}
MAX_COLA = 2000 # memoria limitada del dispositivo: ante un corte muy largo se conservan las mediciones más recientes
LOTE = 150 # mediciones por envío (lotes chicos: cada uno se confirma rápido aunque la base esté en la nube)
LOTES_POR_CICLO = 3
AQUI = Path(__file__).resolve().parent


def ahora_iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DispositivoSimulado:
    def __init__(self, *, api, id_sistema, id_dispositivo, secreto, sensores, dir_local, opciones, log=print, cultivo="", **_extra):
        self.api, self.id_sistema, self.id_dispositivo = api, id_sistema, id_dispositivo
        self.secreto, self.sensores, self.opciones, self.log = secreto, sensores, opciones, log
        self.cultivo = cultivo # cultivo que monitorea este dispositivo (uno por dispositivo)
        h = zlib.crc32(id_dispositivo.encode())             # posición simulada estable por dispositivo (zona de Santiago, norte/sur según la instalación)
        zona = 0.012 if "NORTE" in id_sistema.upper() else -0.012 if "SUR" in id_sistema.upper() else 0.0
        self.gps_base = (-33.4489 + zona + ((h % 1000) / 1000 - 0.5) * 0.002, -70.6693 + (((h >> 10) % 1000) / 1000 - 0.5) * 0.002)
        Path(dir_local).mkdir(parents=True, exist_ok=True)
        self.archivo_cola = Path(dir_local) / f"cola-{id_dispositivo}.json"
        try:
            self.cola = json.loads(self.archivo_cola.read_text()) if self.archivo_cola.exists() else []
        except ValueError:
            self.cola = []
        self.cola = self.cola[-MAX_COLA:] # una cola heredada enorme (horas de corte) bloquearía la sincronización
        self.fallos = 0
        self.proximo_intento = 0
        self.red_caida = False
        self.fin_corte = 0
        self.proximo_corte = 0
        self.t = 0
        self.escenario = None # falla en curso (clave de ESCENARIOS)
        self.escenario_hasta = 0.0
        self.estadisticas = {"generadas": 0, "enviadas": 0, "aceptadas": 0, "duplicadas": 0,
                             "rechazadas": 0, "reintentos": 0}

    """Escritura atómica: nunca queda un archivo a medias."""
    def persistir_cola(self):
        tmp = self.archivo_cola.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.cola))
        os.replace(tmp, self.archivo_cola)

    """Corte de red manual (desde la web o desde código)."""
    def provocar_corte(self, segundos, ahora=None):
        ahora = ahora or int(time.time() * 1000)
        self.red_caida = True
        self.fin_corte = ahora + segundos * 1000
        self.log(f"[{self.id_dispositivo}] SIN CONECTIVIDAD (provocada) por {segundos} s: se almacena localmente")

    def actualizar_red(self, ahora):
        corte_cada = self.opciones.get("corte_cada_seg", 0)
        corte_dur = self.opciones.get("corte_duracion_seg", 0)
        if self.red_caida and ahora >= self.fin_corte: # fin de cualquier corte, automático o manual
            self.red_caida = False
            self.proximo_corte = ahora + corte_cada * 1000 * (0.5 + random.random()) if corte_cada else 0
            self.log(f"[{self.id_dispositivo}] conectividad restablecida: se sincroniza la cola ({len(self.cola)} mediciones)")
            return
        if not corte_cada:
            return
        if not self.proximo_corte:
            self.proximo_corte = ahora + corte_cada * 1000 * (0.5 + random.random())
        if not self.red_caida and ahora >= self.proximo_corte:
            self.provocar_corte(corte_dur, ahora)


    """Provoca una falla concreta del cultivo (falta de agua, gases, pH...). Dura hasta que alguien da la solución
    (ver `reparar`) o hasta que pasan `segundos`."""
    def provocar_escenario(self, nombre, segundos=1800):
        if nombre not in ESCENARIOS:
            raise ValueError(f"Escenario desconocido: {nombre}")
        if nombre == "sin_comunicacion":
            self.provocar_corte(segundos)
            return
        self.escenario, self.escenario_hasta = nombre, time.time() + segundos
        self.log(f"[{self.id_dispositivo}] FALLA SIMULADA: {ESCENARIOS[nombre]['etiqueta']}")

    """Falla al azar (compatibilidad con el botón «Provocar anomalía»)."""
    def provocar_anomalia(self, segundos=180):
        posibles = [k for k, v in ESCENARIOS.items() if k != "sin_comunicacion"
                    and any(x["tipo_variable"] in v["cambios"] for x in self.sensores)]
        self.provocar_escenario(random.choice(posibles or ["falla_bomba"]), segundos)

    """Alguien dio la solución: se termina la falla y vuelve la conexión."""
    def reparar(self):
        self.escenario, self.escenario_hasta = None, 0.0
        if self.red_caida:
            self.red_caida, self.fin_corte = False, 0
            self.log(f"[{self.id_dispositivo}] conexión reparada: se sincroniza la cola ({len(self.cola)} mediciones)")

    def escenario_activo(self):
        if self.escenario and time.time() >= self.escenario_hasta:
            self.escenario = None
        if self.red_caida:
            return "sin_comunicacion"
        return self.escenario

    """Valor simulado alrededor del nivel propio del cultivo (`base` del sensor; si falta, el genérico del tipo)."""
    def valor(self, sensor):
        tipo = sensor["tipo_variable"]
        b = sensor.get("base", BASE.get(tipo, 1))
        v = b * (1 + math.sin(self.t / 12) * 0.08 + (random.random() - 0.5) * 0.04)
        esc = ESCENARIOS.get(self.escenario_activo() or "", {}).get("cambios", {})
        if tipo in esc:
            v = esc[tipo](b) * (1 + (random.random() - 0.5) * 0.02)
        if tipo == "nivel_agua":
            v = min(v, 100.0)
        return round(v, 3)

    def generar(self, ahora):
        self.t += 1
        if (self.opciones.get("anomalias") and not self.escenario and not self.red_caida
                and random.random() < self.opciones.get("prob_falla", 0.0015)): # de vez en cuando aparece una falla sola (~1 por hora y equipo)
            self.provocar_anomalia(150)
        for s in self.sensores:
            self.cola.append({"id_sensor": s["id_sensor"], "tipo_variable": s["tipo_variable"],
                              "valor": self.valor(s), "unidad": s["unidad"],
                              "timestamp": ahora_iso(ahora), "estado_conexion": "conectado"})
            self.estadisticas["generadas"] += 1
        if self.opciones.get("datos_erroneos") and random.random() < 0.08: # datos defectuosos a propósito
            s = self.sensores[0]
            malos = [
                {"id_sensor": "NO_EXISTE", "tipo_variable": s["tipo_variable"], "valor": 1, "unidad": s["unidad"]},
                {"id_sensor": s["id_sensor"], "tipo_variable": s["tipo_variable"], "valor": 1, "unidad": "unidad_incorrecta"},
                {"id_sensor": s["id_sensor"], "tipo_variable": s["tipo_variable"], "valor": "abc", "unidad": s["unidad"]},
            ]
            self.cola.append({**random.choice(malos), "timestamp": ahora_iso(ahora), "estado_conexion": "conectado"})
        if len(self.cola) > MAX_COLA:
            del self.cola[:len(self.cola) - MAX_COLA]
        self.persistir_cola()

    def firmar_y_enviar(self, cuerpo_obj):
        if self.red_caida:
            raise ConnectionError("sin conectividad (simulada)")
        cuerpo = json.dumps(cuerpo_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        firma = hmac.new(self.secreto.encode("utf-8"),
                         f"{ts}.{nonce}.{cuerpo.decode('utf-8')}".encode("utf-8"), hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"{self.api}/api/v1/ingesta", data=cuerpo, method="POST",
            headers={"Content-Type": "application/json", "X-Device-Id": self.id_dispositivo, "X-Timestamp": ts,
                     "X-Nonce": nonce, "X-Signature": firma})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            return e.code, {}

    def vaciar_cola(self, ahora):
        for _ in range(LOTES_POR_CICLO): # varios lotes chicos por ciclo: una cola atrasada se pone al día en segundos
            if not self.cola or ahora < self.proximo_intento or not self._enviar_lote(ahora):
                return

"""Envía un lote. Devuelve True si el servidor lo confirmó."""
    def _enviar_lote(self, ahora):
        lote = self.cola[:LOTE]
        enviar = [({**m, "estado_conexion": "diferido"} if self.fallos > 0 else m) for m in lote]
        try:
            self.estadisticas["enviadas"] += len(enviar)
            calidad = (40 + random.randrange(30)) if self.fallos > 0 else (70 + random.randrange(30))
            status, j = self.firmar_y_enviar({"id_sistema": self.id_sistema, "id_dispositivo": self.id_dispositivo,
                                              "calidad_enlace": calidad, "mediciones": enviar,
                                              "gps": {"lat": round(self.gps_base[0] + random.uniform(-3e-5, 3e-5), 6), "lng": round(self.gps_base[1] + random.uniform(-3e-5, 3e-5), 6),
                                                      "precision": round(3 + random.random() * 6, 1)}})
            if status == 200:
                del self.cola[:len(lote)]
                self.persistir_cola() # solo se borra tras confirmación del servidor
                self.fallos = 0
                self.proximo_intento = 0
                self.estadisticas["aceptadas"] += j.get("aceptadas", 0)
                self.estadisticas["duplicadas"] += j.get("duplicadas", 0)
                self.estadisticas["rechazadas"] += len(j.get("rechazadas", []))
                if j.get("rechazadas") or j.get("alertas_generadas"):
                    motivos = ",".join(x["motivo"] for x in j.get("rechazadas", [])) or "-"
                    alertas = ",".join(a["regla"] for a in j.get("alertas_generadas", [])) or "-"
                    self.log(f"[{self.id_dispositivo}] aceptadas={j.get('aceptadas', 0)} rechazadas={motivos} alertas={alertas}")
                return True
            elif status in (400, 422):
                self.log(f"[{self.id_dispositivo}] el servidor rechazó el lote (HTTP {status}); se descarta para no bloquear la cola")
                del self.cola[:len(lote)]
                self.persistir_cola()
                return True
            else:
                raise ConnectionError(f"HTTP {status}")
        except (OSError, ConnectionError, ValueError) as e:
            self.fallos += 1
            self.estadisticas["reintentos"] += 1
            espera = min(60, 2 ** self.fallos) + random.random()   # backoff exponencial con jitter (segundos)
            self.proximo_intento = ahora + espera * 1000
            if self.fallos == 1 or self.fallos % 5 == 0:
                self.log(f"[{self.id_dispositivo}] envío fallido ({e}); en cola: {len(self.cola)}; reintento en {espera:.1f} s")
            return False

    def ciclo(self):
        ahora = int(time.time() * 1000)
        self.actualizar_red(ahora)
        self.generar(ahora)
        self.vaciar_cola(ahora)


def main():
    p = argparse.ArgumentParser(description="Simulador de dispositivos AquaBrain (Python).")
    p.add_argument("--config", default=str(AQUI.parent / "data" / "dispositivos-demo.json"),
                   help="JSON con los dispositivos (lo genera seed_demo).")
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--intervalo", type=float, default=5)
    p.add_argument("--anomalias", action="store_true")
    p.add_argument("--datos-erroneos", action="store_true")
    p.add_argument("--corte-cada", type=float, default=0)
    p.add_argument("--corte-duracion", type=float, default=40)
    a = p.parse_args()

    archivo = Path(a.config)
    if not archivo.exists():
        sys.exit(f"No existe {archivo}. Ejecuta primero: python manage.py seed_demo")
    opciones = {"anomalias": a.anomalias, "datos_erroneos": a.datos_erroneos,
                "corte_cada_seg": a.corte_cada, "corte_duracion_seg": a.corte_duracion}
    dispositivos = [DispositivoSimulado(api=a.api.rstrip("/"), dir_local=AQUI / ".local", opciones=opciones, **d)
                    for d in json.loads(archivo.read_text())]
    print(f"Simulador: {len(dispositivos)} dispositivo(s) -> {a.api} cada {a.intervalo} s", opciones, flush=True)
    try:
        while True:
            inicio = time.time()
            for d in dispositivos:
                d.ciclo()
            time.sleep(max(0.0, a.intervalo - (time.time() - inicio)))
    except KeyboardInterrupt:
        for d in dispositivos:
            print(d.id_dispositivo, d.estadisticas)


if __name__ == "__main__":
    main()
