from core.tests.base import Base


class VariablesPorCultivoTests(Base):
    """Cada cultivo muestra solo sus variables relevantes (editable en la consola) y una variable oculta no genera alertas."""

    def test_paneles_muestran_solo_las_variables_del_cultivo(self):
        c, _ = self.entrar("supervisor")
        inst = next(i["id"] for i in self.ids(c, "instalaciones"))
        variables = {f["tipo_variable"] for f in c.get("/api/estado-actual", {"instalacion_id": inst}).json()}
        self.assertIn("ec", variables)
        self.assertIn("ppfd", variables)
        for oculta in ("co2", "parametro_cultivo", "nutrientes", "luz_intensidad"):
            self.assertNotIn(oculta, variables)
        self.assertNotIn("co2", {s["tipo_variable"] for s in self.ids(c, "sensores")})
        caudal = next(f for f in c.get("/api/estado-actual", {"instalacion_id": inst}).json() if f["tipo_variable"] == "caudal")
        self.assertTrue(caudal["simulado"])
        self.assertIn("simulación", caudal["tipo_nombre"])

    def test_consola_ve_todos_los_sensores(self):
        c, _ = self.entrar()
        todos = {s["tipo_variable"] for s in self.ids(c, "sensores", todos="1")}
        self.assertIn("co2", todos)

    def test_umbrales_de_referencia_por_cultivo(self):
        c, _ = self.entrar()
        reglas = {r["nombre"]: r for r in self.ids(c, "reglas-alerta") if r["activa"]}
        self.assertEqual(reglas["Lechuga: caudal bajo"]["umbral"], 1.2)
        self.assertEqual(reglas["Tomate cherry: caudal bajo"]["umbral"], 1.92)
        self.assertEqual(reglas["Lechuga: pH demasiado ácido"]["umbral"], 5.0)
        self.assertEqual(reglas["Albahaca: conductividad eléctrica (EC) alta"]["umbral"], 1.6)
        self.assertEqual(reglas["Frutilla: pH demasiado ácido"]["umbral"], 5.5)      # valor único 6,0 ± 0,5
        self.assertNotIn("Tomate cherry: temperatura ambiente alta", reglas)          # sin fuente: no alerta por rango

    def test_variable_oculta_no_genera_alertas_y_se_puede_editar(self):
        c, csrf = self.entrar()
        cid = self.cultivo_id(c, "Lechuga")
        # 1,9 L/min es normal para la lechuga y caudal bajo para el tomate cherry
        self.assertEqual(self.enviar([self.med("CAU1", "caudal", 1.9, "L/min")], cultivo="Lechuga").json()["alertas_generadas"], [])
        self.assertEqual(len(self.enviar([self.med("CAU1", "caudal", 1.9, "L/min")], cultivo="Tomate cherry").json()["alertas_generadas"]), 1)
        # se oculta el pH en la lechuga: un pH ácido ya no alerta ni aparece en el panel
        actuales = next(x for x in self.ids(c, "cultivos") if x["id"] == cid)["variables"]
        r = self.patch(c, csrf, f"/api/cultivos/{cid}", {"variables": [v for v in actuales if v != "ph"]})
        self.assertEqual(r.status_code, 204, r.content)
        self.assertEqual(self.enviar([self.med("PH1", "ph", 3.0, "pH")], cultivo="Lechuga").json()["alertas_generadas"], [])
        self.assertNotIn("ph", {s["tipo_variable"] for s in self.ids(c, "sensores") if s["cultivo_id"] == cid})
        # validación
        self.assertEqual(self.patch(c, csrf, f"/api/cultivos/{cid}", {"variables": []}).status_code, 400)
        self.assertEqual(self.patch(c, csrf, f"/api/cultivos/{cid}", {"variables": ["no_existe"]}).status_code, 400)
