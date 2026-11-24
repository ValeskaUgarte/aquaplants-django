from django.core.management.base import BaseCommand

from core import respaldo


class Command(BaseCommand):
    help = "Respalda la base de datos completa en la carpeta de respaldos (AQ_RESPALDOS_DIR). Con --lista muestra los existentes."

    def add_arguments(self, parser):
        parser.add_argument("--lista", action="store_true", help="Solo listar los respaldos guardados y el espacio usado.")

    def handle(self, *args, **o):
        if not o["lista"]:
            self.stdout.write("Respaldando la base de datos...")
            r = respaldo.crear("manual")
            self.stdout.write(self.style.SUCCESS(
                f"Listo: {r['archivo']}\n  {r['nodos']} nodos · {r['bytes_base'] / 1e6:.1f} MB de datos · "
                f"{r['bytes_archivo'] / 1e6:.1f} MB comprimido · {r['segundos']} s"))
        e = respaldo.estado()
        if e.get("porcentaje") is not None:
            self.stdout.write(f"Espacio usado: {e['tamano_bytes'] / 1e6:.1f} MB de {e['limite_mb']} MB ({e['porcentaje']} %); aviso al {e['aviso_pct']} %.")
        self.stdout.write(f"Carpeta: {e['carpeta']}")
        for r in e["respaldos"]:
            self.stdout.write(f"  {r['archivo']}  {r['bytes'] / 1e6:6.1f} MB")
