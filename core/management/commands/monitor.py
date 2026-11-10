import time

from django.conf import settings
from django.core.management.base import BaseCommand

from core.monitor import ciclo


class Command(BaseCommand):
    help = "Monitor de dispositivos sin comunicación (modo manual: normalmente ya corre dentro de runserver)."

    def handle(self, *args, **options):
        self.stdout.write("Monitor iniciado. Ctrl+C para detener.")
        try:
            while True:
                ciclo()
                time.sleep(settings.AQ["MONITOR_INTERVAL_SECONDS"])
        except KeyboardInterrupt:
            pass
