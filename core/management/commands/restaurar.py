from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from core import respaldo


class Command(BaseCommand):
    help = ("Restaura la base desde un respaldo (archivo de la carpeta de respaldos o ruta completa). Reemplaza el contenido actual "
            "de los nodos restaurados; antes hace un respaldo de seguridad. Detén runserver antes de restaurar.")

    def add_arguments(self, parser):
        parser.add_argument("archivo", nargs="?", help="Respaldo a restaurar (por defecto, el más reciente).")
        parser.add_argument("--nodos", help="Restaurar solo estos nodos, separados por coma (ej.: alertas,mantenciones).")
        parser.add_argument("--si", action="store_true", help="No pedir confirmación.")

    def handle(self, *args, **o):
        archivo = o["archivo"]
        if not archivo:
            lista = respaldo.listar()
            if not lista:
                raise CommandError("No hay respaldos en la carpeta de respaldos.")
            archivo = lista[0]["archivo"]
        try:
            meta = respaldo.leer(archivo)
        except FileNotFoundError as e:
            raise CommandError(f"No existe el respaldo: {archivo}") from e
        nodos = [n.strip() for n in o["nodos"].split(",") if n.strip()] if o["nodos"] else None
        creado = meta["meta"].get("creado_en")
        fecha = datetime.fromtimestamp(creado / 1000).strftime("%d/%m/%Y %H:%M") if creado else "sin fecha"
        self.stdout.write(f"Respaldo: {archivo} ({fecha})\nNodos a reemplazar: {', '.join(nodos or sorted(meta['datos']))}")
        if not o["si"] and input("Se reemplazarán esos datos en la base actual. Escribe RESTAURAR para continuar: ").strip() != "RESTAURAR":
            self.stdout.write("Cancelado.")
            return
        try:
            r = respaldo.restaurar(archivo, nodos)
        except ValueError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Restaurado ({len(r['nodos'])} nodos). Respaldo de seguridad previo: {r['respaldo_previo']}"))
        self.stdout.write("Vuelve a iniciar runserver para que cargue los datos restaurados.")
