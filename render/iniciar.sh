#!/usr/bin/env bash
# Arranque en Render (Start Command: bash render/iniciar.sh).
# Los archivos con secretos NO están en GitHub: se cargan en Render como «Secret Files» y Render los deja en /etc/secrets.
#   firebase-credenciales.json -> se usa directo desde /etc/secrets (AQ_FIREBASE_CREDENCIALES)
#   dispositivos-demo.json     -> secretos de los dispositivos simulados: se copia a data/
set -euo pipefail
mkdir -p data
if [ -f /etc/secrets/dispositivos-demo.json ]; then
  cp /etc/secrets/dispositivos-demo.json data/dispositivos-demo.json
fi
# Un solo proceso: la simulación, el monitor y el relé de la app corren en hilos de este mismo servidor.
exec python manage.py runserver "0.0.0.0:${PORT:-10000}" --noreload
