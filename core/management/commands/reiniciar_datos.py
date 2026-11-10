from django.core.management.base import BaseCommand

from core.rtdb import rtdb


class Command(BaseCommand):
    help = "BORRA todos los datos de la base (Firebase o local) para volver a sembrar la demostración desde cero."

    def add_arguments(self, parser):
        parser.add_argument("--si", action="store_true", help="No pedir confirmación.")

    def handle(self, *args, **opts):
        destino = "la base de Firebase" if rtdb().modo == "firebase" else "la base local"
        if not opts["si"] and input(f"Se borrará TODO el contenido de {destino}. Escribe SI para continuar: ") != "SI":
            self.stdout.write("Cancelado.")
            return
        rtdb().borrar("")
        self.stdout.write(self.style.SUCCESS("Datos borrados. Al iniciar `runserver` se vuelven a sembrar."))
