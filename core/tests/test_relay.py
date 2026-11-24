"""Conexión del celular a través de Firebase (relé), flujo completo de «sin comunicación» y fallas demostrables."""
import json
import secrets
import tempfile
import time
from pathlib import Path

from core import alertas, monitor, relay, repo
from core.rtdb import rtdb
from core.tests.base import Base
from core.util import ahora_ms


def _id():
    return f"{ahora_ms():013d}_{secrets.token_hex(16)}"


class RelayTests(Base):
    """El celular solo escribe/lee Firebase; Django ejecuta la petición con toda su seguridad."""

    def pedir(self, metodo, ruta, cuerpo=None, token=None, csrf=None):
        sid = _id()
        sol = {"m": metodo, "r": ruta, "ts": {".sv": "timestamp"}, "d": "a1b2c3d4e5f6a7b8"}
        if cuerpo is not None:
            sol["b"] = json.dumps(cuerpo)
        if token:
            sol["t"] = token
        if csrf:
            sol["c"] = csrf
        rtdb().set(f"relay/sol/{sid}", sol)                       # lo que hace el celular
        relay._atender(sid, rtdb().get(f"relay/sol/{sid}"))       # lo que hace la escucha del servidor
        r = rtdb().get(f"relay/resp/{sid}")
        self.assertIsNotNone(r, "el servidor no dejó respuesta")
        self.assertIsNone(rtdb().get(f"relay/sol/{sid}"), "la petición debe borrarse tras atenderla")
        return r

    def test_login_y_operacion_por_firebase(self):
        r = self.pedir("POST", "/api/auth/login", {"email": self.correo["administrador"], "password": self.pw["administrador"]})
        self.assertEqual(r["s"], 200, r)
        self.assertTrue(r.get("k"), "el login debe devolver el token de sesión al celular")
        token, csrf = r["k"], json.loads(r["b"])["csrfToken"]
        me = self.pedir("GET", "/api/auth/me", token=token)
        self.assertEqual(json.loads(me["b"])["usuario"]["rol"], "administrador")
        res = json.loads(self.pedir("GET", "/api/movil/resumen", token=token)["b"])
        self.assertEqual(len(res["estados"]), len(res["panel"]["instalaciones"]))
        # escritura: exige el token CSRF igual que en el navegador
        self.assertEqual(self.pedir("PUT", "/api/parametros/minutos_sin_comunicacion", {"valor": "2"}, token=token)["s"], 403)
        self.assertEqual(self.pedir("PUT", "/api/parametros/minutos_sin_comunicacion", {"valor": "2"}, token=token, csrf=csrf)["s"], 204)
        salida = self.pedir("POST", "/api/auth/logout", token=token, csrf=csrf)
        self.assertEqual(salida.get("k"), "")
        self.assertEqual(self.pedir("GET", "/api/auth/me", token=token)["s"], 401)

    def test_sin_sesion_y_rutas_no_permitidas(self):
        self.assertEqual(self.pedir("GET", "/api/panel")["s"], 401)
        self.assertEqual(self.pedir("POST", "/api/v1/ingesta", {})["s"], 400)      # los dispositivos no pasan por el relé

    def test_peticion_vieja_se_descarta(self):
        sid = f"{ahora_ms() - 10 * 60_000:013d}_{secrets.token_hex(16)}"
        rtdb().set(f"relay/sol/{sid}", {"m": "GET", "r": "/api/salud", "ts": ahora_ms() - 10 * 60_000})
        relay._atender(sid, rtdb().get(f"relay/sol/{sid}"))
        self.assertIsNone(rtdb().get(f"relay/resp/{sid}"))

    def test_informe_binario_viaja_en_base64(self):
        r = self.pedir("POST", "/api/auth/login", {"email": self.correo["administrador"], "password": self.pw["administrador"]})
        inf = self.pedir("GET", "/api/informes?tipo=mediciones&formato=xlsx", token=r["k"])
        self.assertEqual(inf["s"], 200, inf)
        self.assertTrue(inf["b64"] and inf["fn"].endswith(".xlsx"))

    def test_reglas_de_firebase_incluyen_el_rele(self):
        reglas = json.loads((Path(__file__).resolve().parents[2] / "firebase" / "database.rules.json").read_text(encoding="utf-8"))
        self.assertIn(".write", reglas["rules"]["relay"]["sol"]["$id"])
        self.assertTrue(reglas["rules"]["relay"]["resp"]["$id"][".read"])
        self.assertFalse(reglas["rules"][".read"])


class SinComunicacionTests(Base):
    """Antes: la alerta de «sin comunicación» nunca se podía confirmar y el dispositivo seguía «sin comunicación»."""

    def test_flujo_completo_hasta_que_el_dispositivo_vuelve(self):
        did = next(d["id"] for d in repo.lista("dispositivos") if d["codigo"] == "AQB-01")
        rtdb().update({f"dispositivos/{did}/ultima_comunicacion": ahora_ms() - 3600_000})
        repo.invalidar("dispositivos")
        self.assertGreaterEqual(monitor.revisar_dispositivos(), 1)
        adm, csrf_a = self.entrar("administrador")
        enc, csrf_e = self.entrar("encargado_cultivo")
        a = next(x for x in adm.get("/api/alertas").json() if x["tipo"] == "sin_comunicacion" and x["dispositivo_codigo"] == "AQB-01")
        self.assertEqual(a["problema"], "Dispositivo sin comunicación")
        self.post(enc, csrf_e, f"/api/alertas/{a['id']}/reconocer")
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{a['id']}/revisar", {"solucionado": True, "nota": "Reinicié el router"}).status_code, 204)
        r = self.post(adm, csrf_a, f"/api/alertas/{a['id']}/verificar", {"aprobada": True})
        self.assertEqual(r.status_code, 409)                                         # todavía no vuelve
        self.assertIn("no vuelve a comunicarse", r.json()["error"]["mensaje"])
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                      # el dispositivo vuelve a enviar: la alerta se resuelve sola
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{a['id']}/verificar", {"aprobada": True, "nota": "Ok"}).status_code, 409)   # ya no hace falta
        self.assertTrue(rtdb().get(f"alertas/{a['id']}/resuelta_auto"))
        cultivo = next(x for i in adm.get("/api/panel").json()["instalaciones"] for x in i["cultivos"] if x["dispositivo_codigo"] == "AQB-01")
        self.assertEqual(cultivo["estado_conexion"], "en_linea")
        self.assertEqual(cultivo["estado_alerta"], "ok")
        self.assertEqual(cultivo["ultima_solucion"]["problema"], "Dispositivo sin comunicación")
        self.assertEqual(cultivo["ultima_solucion"]["solucion_por"], "Encargado Demo")
        self.assertTrue(cultivo["ultima_solucion"]["auto"])

    def test_confirmar_mira_el_valor_actual_aunque_falte_la_marca(self):
        self.enviar([self.med("NIV1", "nivel_agua", 15, "%")])                       # falta de agua
        adm, csrf_a = self.entrar("administrador")
        enc, csrf_e = self.entrar("encargado_cultivo")
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["problema"], "Nivel de agua bajo")
        self.post(enc, csrf_e, f"/api/alertas/{a['id']}/reconocer")
        self.post(enc, csrf_e, f"/api/alertas/{a['id']}/revisar", {"solucionado": True, "nota": "Rellené el estanque"})
        rtdb().update({"ultima/" + rtdb().get(f"alertas/{a['id']}/sensor_id"): {"ts": ahora_ms(), "valor": 80, "conexion": "conectado"}})
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{a['id']}/verificar", {"aprobada": True}).status_code, 204)


class EscenariosTests(Base):
    """Fallas demostrables: cada escenario dispara la alerta que corresponde y `reparar` la termina."""

    def test_cada_escenario_dispara_su_alerta(self):
        from core.simulacion import _modulo_simulador
        mod = _modulo_simulador()
        # solo las fallas de variables del informe (agua, bomba y amoníaco no tienen referencia por cultivo y no se simulan)
        esperado = {"falta_nutrientes": "Conductividad eléctrica (EC) baja", "falta_luz": "Falta de luz (PPFD baja)", "exceso_luz": "Exceso de luz (PPFD alta)",
                    "falta_nitrogeno": "Falta de nitrógeno (N)", "humedad_alta": "Humedad ambiental alta",
                    "humedad_baja": "Humedad ambiental baja", "helada": "Riesgo de helada (temperatura ambiente baja)", "ph_acido": "pH demasiado ácido", "ph_alcalino": "pH demasiado alcalino",
                    "calor": "Temperatura de la solución alta", "exceso_nutrientes": "Conductividad eléctrica (EC) alta"}
        conf = self.por_cultivo["Lechuga"]
        for escenario, problema in esperado.items():
            with self.subTest(escenario=escenario):
                rtdb().borrar("alertas"); rtdb().borrar("alertas_abiertas"); alertas.reiniciar()
                with tempfile.TemporaryDirectory() as tmp:
                    d = mod.DispositivoSimulado(api="http://x", dir_local=tmp, opciones={}, log=lambda *_: None, **conf)
                    d.provocar_escenario(escenario)
                    time.sleep(0.01)
                    valores = [self.med(s["id_sensor"], s["tipo_variable"], d.valor(s), s["unidad"]) for s in d.sensores]
                    self.assertEqual(self.enviar(valores).status_code, 200)
                    problemas = {a.get("problema") for a in rtdb().get("alertas", {}).values()}
                    self.assertEqual(problemas, {problema})                              # una sola alerta clara por falla
                    d.reparar()
                    self.assertIsNone(d.escenario_activo())

    def test_umbrales_del_prototipo_10_y_5_por_ciento_con_severidad(self):
        """ES1: la app AquaBrain_2026 avisa con nivel de agua ≤10 % (advertencia) y ≤5 % (emergencia).
        Un mismo valor que cumple varias reglas hacia el mismo lado genera UNA sola alerta: la más grave."""
        adm, _ = self.entrar("administrador")
        for valor, esperado, severidad in [(4, "Emergencia de agua (≤5 %)", "emergencia"), (8, "Nivel de agua crítico (≤10 %)", "advertencia"),
                                           (30, "Nivel de agua bajo", "advertencia")]:
            with self.subTest(valor=valor):
                rtdb().borrar("alertas"); rtdb().borrar("alertas_abiertas"); alertas.reiniciar()
                self.enviar([self.med("NIV1", "nivel_agua", valor, "%")])
                por_problema = {a["problema"]: a for a in adm.get("/api/alertas").json()}
                self.assertEqual(set(por_problema), {esperado})
                self.assertEqual(por_problema[esperado]["severidad"], severidad)

    def test_al_empeorar_el_nivel_la_alerta_menor_queda_reemplazada(self):
        adm, _ = self.entrar("administrador")
        rtdb().borrar("alertas"); rtdb().borrar("alertas_abiertas"); alertas.reiniciar()
        ahora = int(time.time() * 1000)
        self.enviar([self.med("NIV1", "nivel_agua", 30, "%", ms=ahora - 5000)])
        self.enviar([self.med("NIV1", "nivel_agua", 3, "%", ms=ahora)])
        por_estado = {}
        for a in adm.get("/api/alertas").json():
            por_estado.setdefault(a["estado"] == "resuelta", set()).add(a["problema"])
        self.assertEqual(por_estado.get(False), {"Emergencia de agua (≤5 %)"})      # abierta: solo la más grave
        self.assertEqual(por_estado.get(True), {"Nivel de agua bajo"})              # la menor quedó reemplazada

    def test_humedad_temperatura_ambiente_y_ubicacion(self):
        adm, csrf = self.entrar("administrador")
        tipos = {t["tipo_variable"] for t in adm.get("/api/tipos-sensor").json()}
        self.assertTrue({"humedad", "temperatura_ambiente", "temperatura", "nivel_agua"} <= tipos)   # HU01: temperatura, humedad y nivel de agua
        self.assertTrue(all(i["ubicacion"] for i in adm.get("/api/instalaciones").json()))            # RF-01: nombre y ubicación
        r = self.post(adm, csrf, "/api/instalaciones", {"codigo": "INV-ESTE", "nombre": "Invernadero Este", "ubicacion": "Camino a Lampa km 3"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(next(i for i in adm.get("/api/instalaciones").json() if i["codigo"] == "INV-ESTE")["ubicacion"], "Camino a Lampa km 3")

    def test_cola_heredada_enorme_se_recorta(self):
        from core.simulacion import _modulo_simulador
        mod = _modulo_simulador()
        with tempfile.TemporaryDirectory() as tmp:
            conf = self.por_cultivo["Lechuga"]
            (Path(tmp) / f"cola-{conf['id_dispositivo']}.json").write_text(json.dumps([{"x": i} for i in range(30000)]))
            d = mod.DispositivoSimulado(api="http://x", dir_local=tmp, opciones={}, log=lambda *_: None, **conf)
            self.assertEqual(len(d.cola), mod.MAX_COLA)
            self.assertEqual(d.cola[-1], {"x": 29999})                                # se conservan las más recientes

    def test_panel_muestra_la_falla_y_quien_la_atiende(self):
        self.enviar([self.med("NH31", "amoniaco", 30, "ppm")])
        sup, csrf = self.entrar("supervisor")
        aid = sup.get("/api/alertas").json()[0]["id"]
        self.post(sup, csrf, f"/api/alertas/{aid}/reconocer")
        c = next(x for i in sup.get("/api/panel").json()["instalaciones"] for x in i["cultivos"] if x["dispositivo_codigo"] == "AQB-01")
        self.assertEqual(c["fallas"][0]["problema"], "Gas contaminante amoníaco alto")
        self.assertEqual(c["fallas"][0]["estado"], "reconocida")
        self.assertEqual(c["fallas"][0]["por"], "Supervisor Demo")
