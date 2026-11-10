import json
import secrets
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings

from core import alertas, http, ingesta, repo, rtdb as rtdb_mod, seguridad
from core.management.commands.seed_demo import sembrar
from core.rtdb import rtdb
from core.util import ms_a_iso
from core.vistas import operacion


class Base(SimpleTestCase):
    """Cada prueba parte de una base vacía + datos de demostración, sobre el servidor RTDB local (el mismo cliente REST
    que se usa contra Firebase)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls._cfg = override_settings(AQ={**settings.AQ, "FIREBASE_URL": "", "RTDB_LOCAL_ARCHIVO": "", "RTDB_LOCAL_PUERTO": 0,
                                         "SCRYPT_LOG_N": 10, "DATA_DIR": Path(cls._tmp.name), "RATE_API_POR_MIN": 100000,
                                         "RATE_LOGIN_POR_15MIN": 100000, "RATE_INGEST_POR_MIN": 100000})
        cls._cfg.enable()
        rtdb_mod.reiniciar_cliente()

    @classmethod
    def tearDownClass(cls):
        cls._cfg.disable()
        cls._tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        rtdb().borrar("")
        repo.invalidar(); seguridad.reiniciar_estado(); ingesta.reiniciar_estado(); operacion.invalidar_micro(); http.reiniciar_limites()
        self.cred, self.disp = sembrar()
        self.pw = {c["rol"]: c["password"] for c in self.cred}
        self.correo = {c["rol"]: c["email"] for c in self.cred}
        self.por_cultivo = {d["cultivo"]: d for d in self.disp}                   # un dispositivo por cultivo

    # --- sesión
    def entrar(self, rol="administrador", host=None):
        c = Client(**({"HTTP_HOST": host} if host else {}))
        r = c.post("/api/auth/login", json.dumps({"email": self.correo[rol], "password": self.pw[rol]}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        return c, r.json()["csrfToken"]

    def enviar_api(self, c, csrf, metodo, url, cuerpo=None):
        return getattr(c, metodo)(url, json.dumps(cuerpo if cuerpo is not None else {}), content_type="application/json", HTTP_X_CSRF_TOKEN=csrf)

    def post(self, c, csrf, url, cuerpo=None):
        return self.enviar_api(c, csrf, "post", url, cuerpo)

    def patch(self, c, csrf, url, cuerpo=None):
        return self.enviar_api(c, csrf, "patch", url, cuerpo)

    # --- catálogo
    def ids(self, c, ruta, **filtro):
        return c.get(f"/api/{ruta}", filtro).json()

    def cultivo_id(self, c, nombre):
        return next(x["id"] for x in self.ids(c, "cultivos") if x["nombre"] == nombre)

    def sensor_id(self, c, cultivo, tipo_variable):
        return next(s["id"] for s in self.ids(c, "sensores") if s["cultivo_nombre"] == cultivo and s["tipo_variable"] == tipo_variable)

    # --- dispositivos
    def med(self, codigo, tipo, valor, unidad, ms=None, estado="conectado"):
        return {"id_sensor": codigo, "tipo_variable": tipo, "valor": valor, "unidad": unidad,
                "timestamp": ms_a_iso(ms or int(time.time() * 1000)), "estado_conexion": estado}

    def enviar(self, mediciones, cultivo="Lechuga", ts=None, nonce=None, calidad=80, extra=None, firma_mala=False):
        d = self.por_cultivo[cultivo]
        obj = {"id_sistema": d["id_sistema"], "id_dispositivo": d["id_dispositivo"], "calidad_enlace": calidad, "mediciones": mediciones, **(extra or {})}
        cuerpo = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        ts = str(ts or int(time.time()))
        nonce = nonce or secrets.token_urlsafe(16)
        firma = seguridad.firmar_dispositivo(d["secreto"], ts, nonce, cuerpo)
        return Client().post("/api/v1/ingesta", cuerpo, content_type="application/json", HTTP_X_DEVICE_ID=d["id_dispositivo"],
                             HTTP_X_TIMESTAMP=ts, HTTP_X_NONCE=nonce, HTTP_X_SIGNATURE=("0" * 64 if firma_mala else firma))
