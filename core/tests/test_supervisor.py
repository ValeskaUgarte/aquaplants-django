"""Flujo de alertas con el supervisor cubriendo al encargado, bitácora, estado visible en el dispositivo y sensores nuevos."""
from core.management.commands.seed_demo import completar_sensores
from core.rtdb import rtdb
from core.tests.base import Base


class FlujoSupervisorTests(Base):
    def alerta_caudal(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])                      # caudal bajo: abre la alerta
        adm, _ = self.entrar("administrador")
        return adm, adm.get("/api/alertas").json()[0]["id"]

    def estado_panel(self, c):
        return next(x for i in c.get("/api/panel").json()["instalaciones"] for x in i["cultivos"] if x["dispositivo_codigo"] == "AQB-01")

    def test_supervisor_resuelve_directamente_y_queda_en_la_bitacora(self):
        adm, aid = self.alerta_caudal()
        sup, csrf = self.entrar("supervisor")
        self.assertEqual(self.estado_panel(sup)["estado_alerta"], "activa")
        self.assertEqual(self.post(sup, csrf, f"/api/alertas/{aid}/reconocer").status_code, 204)
        self.assertEqual(self.estado_panel(sup)["estado_alerta"], "reconocida")
        r = self.post(sup, csrf, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Cambié el filtro obstruido"})
        self.assertEqual(r.status_code, 204, r.content)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["estado"], "resuelta")
        self.assertEqual([b["accion"] for b in a["bitacora"]], ["reconoció", "resolvió"])
        self.assertEqual({b["rol"] for b in a["bitacora"]}, {"supervisor"})
        panel = self.estado_panel(adm)
        self.assertEqual(panel["estado_alerta"], "ok")
        self.assertEqual(panel["ultima_solucion"]["por"], "Supervisor Demo")

    def test_supervisor_puede_registrar_que_no_pudo_solucionar(self):
        adm, aid = self.alerta_caudal()
        sup, csrf = self.entrar("supervisor")
        self.post(sup, csrf, f"/api/alertas/{aid}/reconocer")
        self.assertEqual(self.post(sup, csrf, f"/api/alertas/{aid}/revisar", {"solucionado": False, "nota": "La bomba sigue sin presión"}).status_code, 204)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["estado"], "reconocida")                                   # sigue abierta
        self.assertEqual(a["bitacora"][-1]["accion"], "no pudo solucionar")
        self.assertEqual(a["bitacora"][-1]["nota"], "La bomba sigue sin presión")

    def test_encargado_da_solucion_aunque_el_valor_aun_no_se_normalice_y_el_supervisor_confirma(self):
        adm, aid = self.alerta_caudal()
        enc, csrf_e = self.entrar("encargado_cultivo")
        sup, csrf_s = self.entrar("supervisor")
        self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer")
        r = self.post(enc, csrf_e, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Se abrió la válvula principal"})
        self.assertEqual(r.status_code, 204, r.content)                               # antes daba 409 mientras el valor seguía fuera de rango
        self.assertEqual(self.estado_panel(sup)["estado_alerta"], "revisada")
        self.assertEqual(self.post(sup, csrf_s, f"/api/alertas/{aid}/verificar", {"aprobada": True}).status_code, 409)   # aún no se normaliza
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                       # el dispositivo informa el valor normal: se resuelve sola
        self.assertEqual(self.post(sup, csrf_s, f"/api/alertas/{aid}/verificar", {"aprobada": True, "nota": "Confirmado en terreno"}).status_code, 409)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["estado"], "resuelta")
        self.assertEqual([b["accion"] for b in a["bitacora"]], ["reconoció", "dio solución", "se resolvió automáticamente"])
        self.assertEqual(self.estado_panel(enc)["estado_alerta"], "ok")

    def test_alerta_sin_revisar_se_resuelve_sola_cuando_vuelve_a_la_normalidad(self):
        adm, aid = self.alerta_caudal()
        enc, csrf_e = self.entrar("encargado_cultivo")
        self.assertEqual(self.estado_panel(enc)["estado_alerta"], "activa")
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                       # nadie fue a revisar y el valor se normaliza
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual((a["estado"], a["resuelta_auto"]), ("resuelta", True))
        self.assertEqual([(b["por"], b["rol"], b["accion"]) for b in a["bitacora"]], [("Sistema", "sistema", "se resolvió automáticamente")])
        self.assertEqual(adm.get("/api/alertas", {"estado": "abiertas"}).json(), [])
        self.assertEqual(self.estado_panel(enc)["estado_alerta"], "ok")
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer").status_code, 409)       # ya no se pide revisarla
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])                       # si la falla vuelve, nace una alerta nueva
        nuevas = adm.get("/api/alertas", {"estado": "abiertas"}).json()
        self.assertEqual((len(nuevas), nuevas[0]["estado"]), (1, "activa"))
        self.assertNotEqual(nuevas[0]["id"], aid)

    def test_el_administrador_resuelve_su_propia_revision_sin_otra_persona(self):
        adm, aid = self.alerta_caudal()
        adm, csrf_a = self.entrar("administrador")
        self.post(adm, csrf_a, f"/api/alertas/{aid}/reconocer")
        # si no lo logra, él mismo lo deja registrado y la alerta sigue abierta
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{aid}/revisar", {"solucionado": False, "nota": "Falta repuesto"}).status_code, 204)
        self.assertEqual(adm.get("/api/alertas").json()[0]["estado"], "reconocida")
        # si lo soluciona, queda resuelta de inmediato: nadie más debe confirmarlo
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Cambié la manguera"}).status_code, 204)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["estado"], "resuelta")
        self.assertFalse(a["resuelta_auto"])
        self.assertEqual([b["accion"] for b in a["bitacora"]], ["reconoció", "no pudo solucionar", "resolvió"])
        self.assertIn("administrador", a["verificacion_nota"])


class SensoresAmpliadosTests(Base):
    def test_cada_variable_nueva_genera_su_alerta(self):
        casos = [("NIV1", "nivel_agua", 20, "%"), ("LUZ1", "luz_intensidad", 5, "klx"), ("NIT1", "nitrogeno", 30, "ppm"),
                 ("PH1", "ph", 8.5, "pH"), ("CO21", "co2", 2000, "ppm"), ("NH31", "amoniaco", 30, "ppm")]
        r = self.enviar([self.med(c, t, v, u) for c, t, v, u in casos]).json()
        self.assertEqual(len(r["alertas_generadas"]), len(casos), r)

    def test_completar_sensores_agrega_a_una_base_antigua_sin_borrar_y_es_idempotente(self):
        sens = rtdb().get("sensores")
        nuevos = [k for k, v in sens.items() if v["codigo"] in ("NIV1", "LUZ1", "PH1")]
        reglas_viejas = [k for k, v in (rtdb().get("reglas") or {}).items() if v["sensor_id"] in nuevos]
        self.assertTrue(nuevos and reglas_viejas)
        rtdb().update({**{f"sensores/{k}": None for k in nuevos}, **{f"reglas/{k}": None for k in reglas_viejas}})   # imita una base sembrada antes
        usuarios = len(rtdb().get("usuarios"))
        agregados = completar_sensores()
        self.assertGreater(agregados, len(nuevos))
        self.assertEqual(len(rtdb().get("usuarios")), usuarios)                       # no se tocó nada más
        self.assertEqual(sum(v["codigo"] == "NIV1" for v in rtdb().get("sensores").values()), 4)
        self.assertEqual(completar_sensores(), 0)                                    # segunda vez: no repite
