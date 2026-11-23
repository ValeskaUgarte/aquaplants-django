"""Flujo de alertas con el supervisor cubriendo al encargado, bitácora, estado visible en el dispositivo y sensores nuevos."""
from core.management.commands.seed_demo import completar_sensores
from core.rtdb import rtdb
from core.tests.base import Base


class FlujoSupervisorTests(Base):
    def alerta_caudal(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])                      # caudal bajo: abre la alerta
        adm, _ = self.entrar("administrador")
        return adm, adm.get("/api/alertas").json()[0]["id"]

    def avisos(self, c):
        return c.get("/api/notificaciones").json()

    def estado_panel(self, c):
        return next(x for i in c.get("/api/panel").json()["instalaciones"] for x in i["cultivos"] if x["dispositivo_codigo"] == "AQB-01")

    def test_supervisor_resuelve_directamente_y_queda_en_la_bitacora(self):
        adm, aid = self.alerta_caudal()
        sup, csrf = self.entrar("supervisor")
        self.assertEqual(self.estado_panel(sup)["estado_alerta"], "activa")
        self.assertEqual(self.post(sup, csrf, f"/api/alertas/{aid}/reconocer").status_code, 204)
        self.assertEqual(self.estado_panel(sup)["estado_alerta"], "reconocida")
        sid = rtdb().get(f"alertas/{aid}/sensor_id")
        rtdb().update({f"ultima/{sid}/valor": 2.0})                                   # el dato real ya está normal: se acepta de inmediato
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
        self.assertEqual([b["accion"] for b in a["bitacora"]], ["reconoció", "dio solución", "esperando la medición", "se resolvió automáticamente"])
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
        # si lo soluciona y el dato real ya está normal, queda resuelta de inmediato: nadie más debe confirmarlo
        sid = rtdb().get(f"alertas/{aid}/sensor_id")
        rtdb().update({f"ultima/{sid}/valor": 2.0})
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Cambié la manguera"}).status_code, 204)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["estado"], "resuelta")
        self.assertFalse(a["resuelta_auto"])
        self.assertEqual([b["accion"] for b in a["bitacora"]], ["reconoció", "no pudo solucionar", "resolvió"])
        self.assertIn("administrador", a["verificacion_nota"])

    def test_solucion_que_la_medicion_no_respalda_queda_cuestionada_y_se_deriva(self):
        from core import monitor
        adm, aid = self.alerta_caudal()
        enc, csrf_e = self.entrar("encargado_cultivo")
        sup, csrf_s = self.entrar("supervisor")
        self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer")
        # dice «solucionado», pero el valor real sigue fuera de rango: queda esperando la medición
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Ya quedó"}).status_code, 204)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual(a["estado"], "revisada")
        self.assertIsNotNone(a["espera_medicion_hasta"])
        self.assertFalse(a["solucion_cuestionada"])
        self.assertEqual(monitor.revisar_soluciones(), 0)                          # aún dentro del plazo
        rtdb().update({f"alertas/{aid}/espera_medicion_hasta": 1})                 # pasó el plazo y el dato sigue mal
        self.assertEqual(monitor.revisar_soluciones(), 1)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual((a["estado"], a["solucion_cuestionada"]), ("reconocida", True))
        ult = a["bitacora"][-1]
        self.assertEqual((ult["por"], ult["accion"]), ("Sistema", "solución no confirmada por la medición"))
        self.assertIn("Encargado Demo", ult["nota"])
        self.assertIn("Solución no confirmada", " | ".join(n["titulo"] for n in self.avisos(adm)["items"]))
        self.assertTrue(any(r["accion"] == "solucion_cuestionada" for _, r in rtdb().rango("auditoria", ultimos=10)))
        # se deriva a mantención: queda la orden, la bitácora y los avisos; no se puede derivar dos veces seguidas
        r = self.post(enc, csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "bateria", "nota": "El equipo no enciende, parece la batería"})
        self.assertEqual(r.status_code, 201, r.content)
        mid = r.json()["id"]
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "tecnico", "nota": "Otra vez lo mismo"}).status_code, 409)
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "xx", "nota": "no vale"}).status_code, 400)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual((a["mantencion"]["estado"], a["mantencion"]["tipo_texto"]), ("pendiente", "Cambio de batería"))
        self.assertIn("derivó a mantención", [b["accion"] for b in a["bitacora"]])
        self.assertIn("Derivada a técnico", " | ".join(n["titulo"] for n in self.avisos(sup)["items"]))
        # el encargado no gestiona las órdenes; el supervisor sí, y debe contar qué se hizo
        self.assertEqual(self.enviar_api(enc, csrf_e, "patch", f"/api/mantenciones/{mid}", {"estado": "en_curso"}).status_code, 403)
        self.assertEqual(self.enviar_api(sup, csrf_s, "patch", f"/api/mantenciones/{mid}", {"estado": "completada"}).status_code, 400)
        self.assertEqual(self.enviar_api(sup, csrf_s, "patch", f"/api/mantenciones/{mid}", {"estado": "completada", "nota": "Cambié la batería", "tecnico": "Juan Pérez", "resolucion": "reemplazado"}).status_code, 204)
        m = sup.get("/api/mantenciones", {"estado": "completada"}).json()
        self.assertEqual((len(m), m[0]["tecnico"], m[0]["resultado"]), (1, "Juan Pérez", "Cambié la batería"))
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                    # al fin el dato vuelve a la normalidad
        self.assertEqual(adm.get("/api/alertas").json()[0]["estado"], "resuelta")
        # el informe de trabajadores lo refleja: una solución cuestionada y una mantención
        e = next(p for p in adm.get("/api/informes/trabajadores").json()["personas"] if p["rol"] == "encargado_cultivo")
        self.assertEqual((e["soluciones"], e["cuestionadas"], e["efectivas"], e["efectividad"], e["derivaciones"]), (1, 1, 0, 0.0, 1))

    def test_rechazo_del_supervisor_cuestiona_la_solucion(self):
        adm, aid = self.alerta_caudal()
        enc, csrf_e = self.entrar("encargado_cultivo")
        sup, csrf_s = self.entrar("supervisor")
        self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer")
        self.post(enc, csrf_e, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Ya quedó"})
        self.assertEqual(self.post(sup, csrf_s, f"/api/alertas/{aid}/verificar", {"aprobada": False, "nota": "Sigue sin caudal"}).status_code, 204)
        a = adm.get("/api/alertas").json()[0]
        self.assertEqual((a["estado"], a["solucion_cuestionada"], a["requiere_tecnico"]), ("reconocida", True, False))   # umbral: a la primera no exige técnico
        self.post(enc, csrf_e, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Ahora sí"})
        self.post(sup, csrf_s, f"/api/alertas/{aid}/verificar", {"aprobada": False, "nota": "Sigue igual"})
        self.assertTrue(adm.get("/api/alertas").json()[0]["requiere_tecnico"])       # a la segunda se pide un técnico


class SensoresAmpliadosTests(Base):
    def test_cada_variable_nueva_genera_su_alerta(self):
        casos = [("NIV1", "nivel_agua", 20, "%"), ("PPF1", "ppfd", 20, "µmol/m²/s"), ("NIT1", "nitrogeno", 30, "ppm"),
                 ("PH1", "ph", 8.5, "pH"), ("TEM1", "temperatura", 35, "°C"), ("NH31", "amoniaco", 30, "ppm")]
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
