from core.tests.base import Base


class VariablesPorCultivoTests(Base):
    """Cada cultivo muestra solo las variables de su tabla del informe, con su valor de referencia; una variable oculta no genera
    alertas y la lista se puede editar desde la consola."""
    variables_extra = False

    def estado(self, c, cultivo):
        filas = []
        for i in self.ids(c, "instalaciones"):
            filas += c.get("/api/estado-actual", {"instalacion_id": i["id"]}).json()
        return {f["tipo_variable"]: f for f in filas if f["cultivo"] == cultivo}

    def test_paneles_muestran_solo_las_variables_del_informe(self):
        c, _ = self.entrar("supervisor")
        tabla = {"ph", "ec", "temperatura", "temperatura_ambiente", "humedad", "fotoperiodo", "ppfd", "nitrogeno", "fosforo", "potasio", "consumo_hidrico"}
        for cultivo in ("Lechuga", "Albahaca", "Frutilla"):
            self.assertEqual(set(self.estado(c, cultivo)), tabla, cultivo)
        self.assertEqual(set(self.estado(c, "Tomate cherry")), tabla - {"temperatura_ambiente"})   # su tabla no trae temperatura ambiente
        self.assertNotIn("co2", {s["tipo_variable"] for s in self.ids(c, "sensores")})

    def test_cada_variable_trae_su_referencia_del_informe(self):
        c, _ = self.entrar("supervisor")
        lechuga, tomate, frutilla = self.estado(c, "Lechuga"), self.estado(c, "Tomate cherry"), self.estado(c, "Frutilla")
        self.assertEqual(lechuga["ph"]["referencia"], "5,0–7,0")
        self.assertEqual(lechuga["consumo_hidrico"]["referencia"], "75–100 mL/planta/día")
        self.assertEqual(tomate["ec"]["referencia"], "1,6 vegetativo · 3,2–4,2 fructificación")
        self.assertEqual(frutilla["potasio"]["referencia"], "184 mg/L vegetativo · 301 fructificación")
        self.assertEqual(lechuga["nitrogeno"]["unidad"], "mg/L")

    def test_consola_ve_todos_los_sensores(self):
        c, _ = self.entrar()
        todos = {s["tipo_variable"] for s in self.ids(c, "sensores", todos="1")}
        self.assertTrue({"co2", "caudal", "nivel_agua"} <= todos)

    def test_umbrales_de_referencia_por_cultivo(self):
        c, _ = self.entrar()
        reglas = {r["nombre"]: r for r in self.ids(c, "reglas-alerta") if r["activa"]}
        self.assertEqual(reglas["Lechuga: pH demasiado ácido"]["umbral"], 5.0)
        self.assertEqual(reglas["Albahaca: conductividad eléctrica (EC) alta"]["umbral"], 1.6)
        self.assertEqual(reglas["Frutilla: pH demasiado ácido"]["umbral"], 5.5)      # valor único 6,0 ± 0,5
        self.assertNotIn("Tomate cherry: temperatura ambiente alta", reglas)          # sin fuente: no alerta por rango
        self.assertNotIn("Lechuga: caudal bajo", reglas)                              # sin referencia en el informe
        self.assertNotIn("Lechuga: nivel de agua bajo", reglas)

    def test_variable_oculta_no_genera_alertas_y_se_puede_editar(self):
        c, csrf = self.entrar()
        cid = self.cultivo_id(c, "Lechuga")
        self.assertEqual(self.enviar([self.med("CAU1", "caudal", 0.1, "L/min")], cultivo="Lechuga").json()["alertas_generadas"], [])
        # se oculta el pH en la lechuga: un pH ácido ya no alerta ni aparece en el panel
        self.assertEqual(len(self.enviar([self.med("PH1", "ph", 3.0, "pH")], cultivo="Lechuga").json()["alertas_generadas"]), 1)
        actuales = next(x for x in self.ids(c, "cultivos") if x["id"] == cid)["variables"]
        r = self.patch(c, csrf, f"/api/cultivos/{cid}", {"variables": [v for v in actuales if v != "ph"]})
        self.assertEqual(r.status_code, 204, r.content)
        self.assertEqual(self.enviar([self.med("PH1", "ph", 2.5, "pH")], cultivo="Lechuga").json()["alertas_generadas"], [])
        self.assertNotIn("ph", {s["tipo_variable"] for s in self.ids(c, "sensores") if s["cultivo_id"] == cid})
        # validación
        self.assertEqual(self.patch(c, csrf, f"/api/cultivos/{cid}", {"variables": []}).status_code, 400)
        self.assertEqual(self.patch(c, csrf, f"/api/cultivos/{cid}", {"variables": ["no_existe"]}).status_code, 400)

    def test_simulacion_solo_ofrece_fallas_que_alertan(self):
        from core.simulacion import escenarios
        codigos = {e["codigo"] for e in escenarios()}
        self.assertTrue({"ph_acido", "falta_luz", "falta_nitrogeno", "sin_comunicacion"} <= codigos)
        self.assertFalse({"falta_agua", "falla_bomba", "gas_amoniaco", "exceso_agua", "agua_emergencia"} & codigos)
