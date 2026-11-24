"""Asegura los usuarios de demostración adicionales (idempotente: no repite ni cambia los que ya existen).

Crea «Encargado Demo 2», un segundo encargado de cultivo con acceso a TODAS las instalaciones, es decir, a los 4 dispositivos
(AQB-01..04), para que reciba las alertas de todos los cultivos. El encargado original sigue viendo solo Invernadero Norte.
Sirve también contra una base ya sembrada: `python manage.py usuarios_demo`.
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core import acceso, repo, seguridad
from core.rtdb import rtdb

ADICIONALES = [("encargado2@aquaplants.demo", "Encargado Demo 2", "encargado_cultivo"),
               ("tecnico@aquaplants.demo", "Técnico Demo", "tecnico")]


def asegurar_usuarios_demo():
    """Devuelve la lista de usuarios creados ahora: [{email, rol, password}]."""
    if not rtdb().get("usuarios"):
        return []                                    # base vacía: aún no se ha sembrado
    repo.invalidar("usuarios", "instalaciones")
    todas = list(repo.col("instalaciones").keys())
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


class Command(BaseCommand):
    help = "Crea los usuarios de demostración adicionales (segundo encargado con los 4 dispositivos) si faltan."

    def handle(self, *args, **options):
        creados = asegurar_usuarios_demo()
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
