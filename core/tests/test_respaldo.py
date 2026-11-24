"""Respaldo, restauración, rotación de respaldos y aviso de espacio de la base (plan de mitigación)."""
import gzip
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.test import override_settings

from core import repo, respaldo
from core.rtdb import rtdb
from core.tests.base import Base


class RespaldoTests(Base):
    def setUp(self):
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        self._cfg2 = override_settings(AQ={**settings.AQ, "RESPALDOS_DIR": self._dir.name})
        self._cfg2.enable()

    def tearDown(self):
        self._cfg2.disable()
        self._dir.cleanup()
        super().tearDown()

    def test_respaldo_completo_y_restauracion_tras_perdida(self):
        r = respaldo.crear("prueba")
        self.assertTrue(Path(r["archivo"]).exists())
        with gzip.open(r["archivo"], "rb") as f:
            contenido = json.loads(f.read())
        self.assertIn("usuarios", contenido["datos"])
        self.assertEqual(contenido["meta"]["motivo"], "prueba")
        usuarios, cultivos = rtdb().get("usuarios"), rtdb().get("cultivos")
        rtdb().borrar("usuarios")                                    # pérdida de datos
        rtdb().borrar("cultivos")
        res = respaldo.restaurar(Path(r["archivo"]).name)
        self.assertEqual(rtdb().get("usuarios"), usuarios)
        self.assertEqual(rtdb().get("cultivos"), cultivos)
        self.assertTrue(Path(res["respaldo_previo"]).exists())     # antes de restaurar se respaldó el estado dañado
        self.assertNotEqual(res["respaldo_previo"], r["archivo"])   # ...en otro archivo: el respaldo restaurado queda intacto
        self.assertIn("usuarios", respaldo.leer(r["archivo"])["datos"])

    def test_restaurar_solo_un_nodo_no_toca_los_demas(self):
        r = respaldo.crear()
        rtdb().borrar("reglas")
        rtdb().set("parametros/minutos_sin_comunicacion", {"valor": "9"})
        respaldo.restaurar(r["archivo"], ["reglas"], respaldo_previo=False)
        self.assertTrue(rtdb().get("reglas"))
        self.assertEqual(rtdb().get("parametros/minutos_sin_comunicacion/valor"), "9")
        with self.assertRaises(ValueError):
            respaldo.restaurar(r["archivo"], ["no_existe"], respaldo_previo=False)

    def test_rotacion_conserva_los_recientes_y_uno_por_semana(self):
        repo.guardar("parametros", "respaldos_conservar", {"valor": "3"})
        carpeta = Path(self._dir.name)
        ahora = datetime.now()
        for d in range(0, 40, 2):                                    # un respaldo cada 2 días durante 40 días
            (carpeta / f"respaldo_{ahora - timedelta(days=d):%Y%m%d_%H%M%S}.json.gz").write_bytes(b"x")
        respaldo.rotar()
        quedan = respaldo.listar()
        self.assertEqual([r["archivo"] for r in quedan[:3]], sorted([r["archivo"] for r in quedan], reverse=True)[:3])
        self.assertLessEqual(len(quedan), 3 + 5)
        self.assertTrue(all(r["fecha"] >= ahora - timedelta(weeks=4, days=1) for r in quedan))

    def test_aviso_de_espacio_a_los_administradores_una_vez_al_dia(self):
        repo.guardar("parametros", "limite_base_mb", {"valor": "1"})
        adm, _ = self.entrar("administrador")
        sup, _ = self.entrar("supervisor")
        self.assertGreaterEqual(respaldo.revisar_espacio(900_000), 80)
        respaldo.revisar_espacio(950_000)                            # no repite el aviso el mismo día
        avisos = [n for n in adm.get("/api/notificaciones").json()["items"] if n["tipo"] == "espacio_base"]
        self.assertEqual(len(avisos), 1)
        self.assertFalse([n for n in sup.get("/api/notificaciones").json()["items"] if n["tipo"] == "espacio_base"])

    def test_api_solo_administrador_y_parametros_validados(self):
        sup, csrf_s = self.entrar("supervisor")
        self.assertEqual(sup.get("/api/respaldos").status_code, 403)
        adm, csrf = self.entrar("administrador")
        e = adm.get("/api/respaldos").json()
        self.assertEqual(e["limite_mb"], 1024)
        self.assertEqual(self.enviar_api(adm, csrf, "put", "/api/parametros/aviso_espacio_pct", {"valor": "150"}).status_code, 400)
        self.assertEqual(self.enviar_api(adm, csrf, "put", "/api/parametros/respaldo_cada_horas", {"valor": "12"}).status_code, 204)
        self.assertEqual(adm.get("/api/respaldos").json()["cada_horas"], 12)
