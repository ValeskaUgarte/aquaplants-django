"""Asegura los usuarios de demostración adicionales (idempotente: no repite ni cambia los que ya existen).

Crea «Encargado Demo 2», un segundo encargado de cultivo con acceso a TODAS las instalaciones, es decir, a los 4 dispositivos
(AQB-01..04), para que reciba las alertas de todos los cultivos. El encargado original sigue viendo solo Invernadero Norte.
Sirve también contra una base ya sembrada: `python manage.py usuarios_demo`.
"""
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core import acceso, repo, seguridad
from core.rtdb import rtdb

CODIGO_BODEGA, CODIGO_EQUIPO_VENTA = "INV-STOCK", "AQB-05"
CLAVE_ACTIVACION_DEMO = "AQUA-DEMO-2026"           # solo en demostración; se puede cambiar con AQ_DEMO_CLAVE_ACTIVACION en el .env
ADICIONALES = [("encargado2@aquaplants.demo", "Encargado Demo 2 (todos los cultivos)", "encargado_cultivo"),
               ("tecnico@aquaplants.demo", "Técnico Demo (servicio técnico)", "tecnico")]


def asegurar_usuarios_demo():
    """Devuelve la lista de usuarios creados ahora: [{email, rol, password}]."""
    if not rtdb().get("usuarios"):
        return []                                    # base vacía: aún no se ha sembrado
    repo.invalidar("usuarios", "instalaciones")
    # El encargado demo ve las instalaciones de la empresa; no la bodega ni las de clientes particulares.
    todas = [i for i, x in repo.col("instalaciones").items() if x.get("codigo") != CODIGO_BODEGA and not x.get("propietario_id")]
    creados = []
    for email, nombre, rol in ADICIONALES:
        existente = repo.usuario_por_email(email)
        if existente:
            if rol == "tecnico":
                continue                             # el técnico no se asocia a instalaciones: recibe solicitudes
            faltan = [i for i in todas if i not in (existente.get("instalaciones") or {})]
            if faltan:                               # si se creó una instalación nueva, también la ve
                repo.actualizar("usuarios", existente["id"], {"instalaciones": {i: True for i in todas}})
            continue
        pw = acceso.clave_demo() or seguridad.generar_password_demo()
        repo.crear_usuario(email, nombre, rol, seguridad.hash_password(pw), [] if rol == "tecnico" else todas)
        creados.append({"email": email, "rol": rol, "password": pw})
    return creados


def asegurar_equipo_venta_demo():
    """Deja un equipo de ejemplo «en venta» (AQB-05, en la bodega) con su clave de activación, para probar el registro de un cliente particular.
    Idempotente. Solo en demostración (no en producción). Devuelve (codigo, clave) si lo creó ahora, o None."""
    if settings.AQ["PRODUCCION"] or not rtdb().get("usuarios"):
        return None
    from core.management.commands.seed_demo import INSTALACIONES, SENSORES, _reglas_del_cultivo, niveles_completos
    from core.util import ahora_ms
    from core.vistas.clientes import hash_clave
    repo.invalidar("dispositivos", "instalaciones", "tipos_sensor")
    if any(d.get("codigo") == CODIGO_EQUIPO_VENTA for d in repo.lista("dispositivos")):
        return None
    tipos = repo.col("tipos_sensor")
    if any(t not in tipos for _, t in SENSORES):
        return None                                  # la base aún no tiene todos los tipos de sensor (se agregan al iniciar)
    ahora = ahora_ms()
    bodega = next((i for i, x in repo.col("instalaciones").items() if x.get("codigo") == CODIGO_BODEGA), None)
    mapa = {}
    if bodega is None:
        bodega = repo.nuevo_id()
        mapa[f"instalaciones/{bodega}"] = {"codigo": CODIGO_BODEGA, "nombre": "Bodega AquaPlants (equipos sin vender)", "activa": True, "creada_en": ahora,
                                           "descripcion": "Equipos nuevos que todavía no activó ningún cliente (demostración).", "ubicacion": "Bodega (simulado)"}
    niveles = INSTALACIONES[0][3][0]["niveles"]
    cid, did, secreto = repo.nuevo_id(), repo.nuevo_id(), seguridad.token_aleatorio(32)
    clave = os.environ.get("AQ_DEMO_CLAVE_ACTIVACION", "").strip() or CLAVE_ACTIVACION_DEMO
    mapa[f"cultivos/{cid}"] = {"instalacion_id": bodega, "nombre": "Equipo sin activar", "descripcion": "Equipo en venta", "activo": True}
    mapa[f"dispositivos/{did}"] = {"instalacion_id": bodega, "cultivo_id": cid, "codigo": CODIGO_EQUIPO_VENTA, "nombre": "AquaBrain · equipo en venta",
                                   "secreto_cif": seguridad.cifrar(secreto, f"dispositivo:{did}"), "activo": True, "estado_conexion": "en_linea",
                                   "ultima_comunicacion": ahora, "calidad_enlace": 85, "creado_en": ahora, "clave_activacion_hash": hash_clave(clave)}
    base, sim, ids = niveles_completos(niveles), [], {}
    for cod, tipo in SENSORES:
        sid = repo.nuevo_id()
        ids[cod] = sid
        mapa[f"sensores/{sid}"] = {"dispositivo_id": did, "tipo_sensor_id": tipo, "codigo": cod, "nombre": f"{tipos[tipo]['nombre']} · Mi cultivo", "activo": True}
        mapa[f"ultima/{sid}"] = {"ts": ahora - 5_000, "valor": base[tipo], "conexion": "conectado"}
        sim.append({"id_sensor": cod, "tipo_variable": tipo, "unidad": tipos[tipo]["unidad"], "base": base[tipo]})
    for cod_sensor, nombre_regla, op, umbral, *sev in _reglas_del_cultivo("Mi cultivo", niveles):
        mapa[f"reglas/{repo.nuevo_id()}"] = {"nombre": nombre_regla, "sensor_id": ids[cod_sensor], "operador": op, "umbral": umbral, "activa": True,
                                             **({"severidad": sev[0]} if sev else {})}
    rtdb().update(mapa)
    repo.invalidar()
    archivo = Path(settings.AQ["DATA_DIR"]) / "dispositivos-demo.json"          # el simulador envía datos de este equipo con su secreto
    if archivo.exists():
        config = json.loads(archivo.read_text(encoding="utf-8"))
        if not any(x.get("id_dispositivo") == CODIGO_EQUIPO_VENTA for x in config):
            config.append({"id_sistema": CODIGO_BODEGA, "id_dispositivo": CODIGO_EQUIPO_VENTA, "secreto": secreto, "cultivo": "Mi cultivo", "sensores": sim})
            archivo.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return CODIGO_EQUIPO_VENTA, clave


class Command(BaseCommand):
    help = "Crea los usuarios de demostración adicionales (segundo encargado con los 4 dispositivos) si faltan."

    def handle(self, *args, **options):
        creados = asegurar_usuarios_demo()
        venta = asegurar_equipo_venta_demo()
        if venta:
            self.stdout.write(self.style.SUCCESS(
                f"Equipo de ejemplo para un cliente particular: código {venta[0]}  clave de activación {venta[1]}  (en «Crear cuenta» del ingreso)."))
        if not creados:
            self.stdout.write("Usuarios de demostración al día.")
            return
        archivo = Path(settings.AQ["DATA_DIR"]) / "credenciales-demo.txt"
        previo = archivo.read_text(encoding="utf-8") if archivo.exists() else ""
        if previo and not previo.endswith("\n"):
            previo += "\n"
        archivo.write_text(previo + "".join(f"{c['rol']}\t{c['email']}\t{c['password']}\n" for c in creados), encoding="utf-8")
        try:
            os.chmod(archivo, 0o600)
        except OSError:
            pass
        for c in creados:
            self.stdout.write(self.style.SUCCESS(f"Usuario creado: {c['email']}  clave: {c['password']}  (también en {archivo})"))
