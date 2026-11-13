"""Pruebas de las correcciones: alertas que se generan y notifican, segundo encargado, informes, vinculación de equipos."""
import io
import json

from django.core.management import call_command
from django.test import Client

from core.management.commands.usuarios_demo import asegurar_usuarios_demo
from core.rtdb import rtdb
from core.tests.base import Base


class MejorasBase(Base):
    def setUp(self):
        super().setUp()
        extra = asegurar_usuarios_demo()
        self.pw2 = extra[0]["password"]

    def entrar2(self):
        c = Client()
        r = c.post("/api/auth/login", json.dumps({"email": "encargado2@aquaplants.demo", "password": self.pw2}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        return c, r.json()["csrfToken"]

    def avisos(self, c):
        return c.get("/api/notificaciones").json()


class AlertasYAvisosTests(MejorasBase):
    def test_se_generan_alertas_nuevas_y_avisan_a_quien_corresponde(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")], cultivo="Lechuga")          # Invernadero Norte
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")], cultivo="Tomate cherry")    # Invernadero Sur
        enc, _ = self.entrar("encargado_cultivo")
        enc2, _ = self.entrar2()
        adm, _ = self.entrar("administrador")
        sup, _ = self.entrar("supervisor")
        self.assertEqual(self.avisos(enc)["no_leidas"], 1)          # el encargado original solo ve el Norte
        self.assertEqual(self.avisos(enc2)["no_leidas"], 2)         # el segundo encargado, los 4 dispositivos
        self.assertEqual(self.avisos(adm)["no_leidas"], 2)
        self.assertEqual(self.avisos(sup)["no_leidas"], 2)
        # una segunda alerta distinta (otro sensor) también se crea: ya no «solo fue 1»
        self.enviar([self.med("NUT1", "nutrientes", 100, "ppm")], cultivo="Lechuga")
        self.assertEqual(len(adm.get("/api/alertas", {"estado": "abiertas"}).json()), 3)

    def test_alerta_bloqueada_por_clave_huerfana_se_limpia(self):
        from core import alertas
        rtdb().set("alertas_abiertas/u:fantasma:x", "no-existe")
        alertas.reiniciar()
        r = self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")]).json()
        self.assertEqual(len(r["alertas_generadas"]), 1)
        self.assertIsNone(rtdb().get("alertas_abiertas/u:fantasma:x"))

    def test_administrador_recibe_los_comentarios_de_encargado_y_supervisor(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        enc, csrf_e = self.entrar("encargado_cultivo")
        adm, _ = self.entrar("administrador")
        sup, csrf_s = self.entrar("supervisor")
        aid = adm.get("/api/alertas").json()[0]["id"]
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer").status_code, 204)
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Se limpió el filtro de la bomba"}).status_code, 204)
        textos = " | ".join(n["mensaje"] for n in self.avisos(adm)["items"])
        self.assertIn("Se limpió el filtro de la bomba", textos)
        self.assertIn(aid, [n["alerta_id"] for n in self.avisos(sup)["items"]])
        # el valor sigue fuera de rango: el supervisor no puede confirmar, pero sí rechazar con su motivo
        self.assertEqual(self.post(sup, csrf_s, f"/api/alertas/{aid}/verificar", {"aprobada": True, "nota": "Todo en orden"}).status_code, 409)
        self.assertEqual(self.post(sup, csrf_s, f"/api/alertas/{aid}/verificar", {"aprobada": False, "nota": "Sigue sin presión"}).status_code, 204)
        self.assertIn("Sigue sin presión", " | ".join(n["mensaje"] for n in self.avisos(adm)["items"]))
        self.assertIn("Sigue sin presión", " | ".join(n["mensaje"] for n in self.avisos(enc)["items"]))   # el encargado se entera del resultado
        # no se notifica a quien ejecuta la acción
        self.assertNotIn("Se limpió el filtro de la bomba", " | ".join(n["mensaje"] for n in self.avisos(enc)["items"]))
        self.assertEqual(adm.get("/api/seguimiento-alertas").json()["conteo"]["pendientes"], 1)
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                      # cuando de verdad se arregla, se resuelve sola
        seg = adm.get("/api/seguimiento-alertas").json()
        self.assertEqual(seg["conteo"]["pendientes"], 0)
        self.assertEqual(seg["resueltas"][0]["revision_nota"], "Se limpió el filtro de la bomba")
        self.assertEqual(seg["resueltas"][0]["verificacion_nota"], "Sigue sin presión")
        self.assertTrue(seg["resueltas"][0]["resuelta_auto"])

    def test_marcar_leidas(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        adm, csrf = self.entrar("administrador")
        self.assertEqual(self.avisos(adm)["no_leidas"], 1)
        self.assertEqual(self.post(adm, csrf, "/api/notificaciones/leer", {}).status_code, 204)
        self.assertEqual(self.avisos(adm)["no_leidas"], 0)

    def test_roles_del_flujo(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        adm, _ = self.entrar("administrador")
        aid = adm.get("/api/alertas").json()[0]["id"]
        sup, csrf = self.entrar("supervisor")
        self.assertEqual(self.post(sup, csrf, f"/api/alertas/{aid}/reconocer").status_code, 204)     # el supervisor cubre al encargado
        enc, csrf_e = self.entrar("encargado_cultivo")
        self.assertEqual(self.post(enc, csrf_e, f"/api/alertas/{aid}/verificar", {"aprobada": True}).status_code, 403)


    def test_administrador_resuelve_su_propia_revision(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        adm, csrf_a = self.entrar("administrador")
        aid = adm.get("/api/alertas").json()[0]["id"]
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{aid}/reconocer").status_code, 204)
        # el propio administrador decide: «no pude solucionarlo» deja la alerta abierta ...
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{aid}/revisar", {"solucionado": False, "nota": "Falta probar de nuevo"}).status_code, 204)
        self.assertEqual(rtdb().get(f"alertas/{aid}/estado"), "reconocida")
        # ... y «quedó solucionado» la resuelve sin pedir la confirmación de nadie más, siempre que la medición no lo contradiga
        sid = rtdb().get(f"alertas/{aid}/sensor_id")
        rtdb().update({f"ultima/{sid}/valor": 2.0})                                  # el dato real ya está normal (sin pasar por la ingesta)
        self.assertEqual(self.post(adm, csrf_a, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Ahora sí quedó"}).status_code, 204)
        a = rtdb().get(f"alertas/{aid}")
        self.assertEqual(a["estado"], "resuelta")
        self.assertIn("administrador", a["verificacion_nota"])


class SegundoEncargadoTests(MejorasBase):
    def test_ve_los_cuatro_dispositivos(self):
        c, _ = self.entrar2()
        insts = c.get("/api/instalaciones").json()
        self.assertEqual(len(insts), 2)
        codigos = set()
        for i in insts:
            codigos |= {f["dispositivo_codigo"] for f in c.get("/api/estado-actual", {"instalacion_id": i["id"]}).json()}
        self.assertEqual(codigos, {"AQB-01", "AQB-02", "AQB-03", "AQB-04"})
        original, _ = self.entrar("encargado_cultivo")
        self.assertEqual(len(original.get("/api/instalaciones").json()), 1)

    def test_comando_es_idempotente(self):
        self.assertEqual(asegurar_usuarios_demo(), [])
        call_command("usuarios_demo", stdout=io.StringIO())


class InformesTests(MejorasBase):
    def test_excel_con_historial_y_seguimiento(self):
        from openpyxl import load_workbook
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        adm, csrf = self.entrar("administrador")
        aid = adm.get("/api/alertas").json()[0]["id"]
        enc, csrf_e = self.entrar("encargado_cultivo")
        self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer")
        r = adm.get("/api/informes", {"agregacion": "crudo"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("attachment", r["Content-Disposition"])
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Resumen", "Mediciones", "Alertas", "Eventos", "Trabajadores", "Detalle de gestión", "Mantenciones"])
        filas = list(wb["Mediciones"].iter_rows(values_only=True))
        self.assertEqual(filas[0][:3], ("Fecha y hora", "Instalación", "Cultivo"))
        self.assertGreater(len(filas), 100)
        al = list(wb["Alertas"].iter_rows(values_only=True))
        self.assertEqual(al[0][1], "Estado")
        self.assertTrue(any("PENDIENTE" in str(f[-1]) for f in al[1:]))

    def test_informe_grande_se_agrupa_solo_en_vez_de_fallar(self):
        from unittest.mock import patch
        from openpyxl import load_workbook
        from urllib.parse import unquote
        from core import informes
        adm, _ = self.entrar("administrador")
        cid = self.cultivo_id(adm, "Tomate cherry")
        with patch.dict(informes.TOPE_FILAS, {"xlsx": 40, "csv": 40}):
            r = adm.get("/api/informes", {"agregacion": "crudo", "tipo": "mediciones", "cultivo_id": cid})
        self.assertEqual(r.status_code, 200, r.content)                      # antes: 413 «supera las filas permitidas»
        self.assertIn("se agruparon", unquote(r["X-Informe-Aviso"]))
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(next(wb["Mediciones"].iter_rows(values_only=True))[0], "Inicio del tramo")
        self.assertTrue(any("Nota" == c.value for c in wb["Resumen"]["A"]))

    def test_informe_de_trabajadores(self):
        from openpyxl import load_workbook
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        adm, csrf_a = self.entrar("administrador")
        enc, csrf_e = self.entrar("encargado_cultivo")
        aid = adm.get("/api/alertas").json()[0]["id"]
        self.post(enc, csrf_e, f"/api/alertas/{aid}/reconocer")
        self.post(enc, csrf_e, f"/api/alertas/{aid}/revisar", {"solucionado": True, "nota": "Se limpió el filtro"})
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                # la medición lo respalda: queda resuelta
        j = adm.get("/api/informes/trabajadores").json()
        e = next(p for p in j["personas"] if p["rol"] == "encargado_cultivo")
        self.assertEqual((e["reconocidas"], e["soluciones"], e["efectivas"], e["cuestionadas"], e["efectividad"]), (1, 1, 1, 0, 100.0))
        self.assertEqual(e["cumplimiento"], 100.0)
        r = adm.get("/api/informes", {"tipo": "trabajadores"})
        self.assertEqual(r.status_code, 200, r.content)
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Resumen", "Trabajadores", "Detalle de gestión", "Mantenciones"])
        det = list(wb["Detalle de gestión"].iter_rows(values_only=True))
        self.assertTrue(any(f[6] == "dio solución" and str(f[8]).startswith("Efectiva") for f in det[1:]))
        self.assertEqual(adm.get("/api/informes", {"tipo": "trabajadores", "formato": "csv"}).status_code, 200)
        self.assertEqual(enc.get("/api/informes/trabajadores").status_code, 403)

    def test_filtros_y_csv(self):
        adm, _ = self.entrar("administrador")
        cid = self.cultivo_id(adm, "Tomate cherry")
        r = adm.get("/api/informes", {"formato": "csv", "tipo": "mediciones", "cultivo_id": cid, "agregacion": "1h"})
        self.assertEqual(r.status_code, 200)
        texto = r.content.decode("utf-8-sig")
        self.assertIn("Tomate cherry", texto)
        self.assertNotIn("Lechuga", texto)
        self.assertEqual(texto.splitlines()[0].split(";")[0], "Inicio del tramo")

    def test_validaciones_y_permisos(self):
        adm, _ = self.entrar("administrador")
        self.assertEqual(adm.get("/api/informes", {"desde": "ayer"}).status_code, 400)
        self.assertEqual(adm.get("/api/informes", {"desde": "2026-02-01", "hasta": "2026-01-01"}).status_code, 400)
        self.assertEqual(adm.get("/api/informes", {"desde": "2024-01-01", "hasta": "2026-01-01"}).status_code, 400)
        self.assertEqual(adm.get("/api/informes", {"tipo": "otro"}).status_code, 400)
        for rol in ("supervisor", "encargado_cultivo"):
            c, _ = self.entrar(rol)
            self.assertEqual(c.get("/api/informes").status_code, 403)


class VinculacionTests(MejorasBase):
    def registrar(self, c, csrf, **cuerpo):
        return self.post(c, csrf, "/api/vinculacion/registrar", {"plataforma": "web", **cuerpo})

    def test_vincula_reconoce_el_mismo_equipo_y_lista(self):
        c, csrf = self.entrar("encargado_cultivo")
        r = self.registrar(c, csrf)
        self.assertEqual(r.status_code, 201)
        eq = r.json()
        self.assertTrue(eq["secreto"])
        r2 = self.registrar(c, csrf, id=eq["id"], secreto=eq["secreto"])
        self.assertEqual((r2.status_code, r2.json()["id"], "secreto" in r2.json()), (200, eq["id"], False))
        lista = c.get("/api/vinculacion/mis-equipos").json()
        self.assertEqual(len(lista), 1)
        self.assertTrue(lista[0]["actual"])
        # un secreto equivocado no suplanta al equipo: se crea otro
        r3 = self.registrar(c, csrf, id=eq["id"], secreto="x" * 30)
        self.assertEqual(r3.status_code, 201)
        self.assertNotEqual(r3.json()["id"], eq["id"])

    def test_push_token_y_desvincular(self):
        c, csrf = self.entrar("encargado_cultivo")
        eq = self.registrar(c, csrf, plataforma="android", nombre="Samsung A54", push_token="t" * 60).json()
        uid = [u for u in rtdb().get("usuarios").items() if u[1]["email"] == self.correo["encargado_cultivo"]][0][0]
        self.assertEqual(len(rtdb().get(f"push_tokens/{uid}")), 1)
        otro, csrf2 = self.entrar("encargado_cultivo")
        self.registrar(otro, csrf2)
        self.assertEqual(self.post(otro, csrf2, f"/api/vinculacion/{eq['id']}/desvincular").status_code, 204)
        self.assertIsNone(rtdb().get(f"push_tokens/{uid}"))
        self.assertEqual(c.get("/api/auth/me").status_code, 401)      # la sesión del equipo desvinculado se cerró

    def test_administrador_revoca_y_el_equipo_queda_bloqueado(self):
        c, csrf = self.entrar("supervisor")
        eq = self.registrar(c, csrf).json()
        adm, csrf_a = self.entrar("administrador")
        todos = adm.get("/api/equipos").json()
        self.assertEqual(todos[0]["email"], self.correo["supervisor"])
        self.assertEqual(self.post(adm, csrf_a, f"/api/equipos/{eq['id']}/revocar").status_code, 204)
        self.assertEqual(c.get("/api/auth/me").status_code, 401)
        c2, csrf2 = self.entrar("supervisor")
        r = self.registrar(c2, csrf2, id=eq["id"], secreto=eq["secreto"])
        self.assertEqual((r.status_code, r.json()["error"]["codigo"]), (403, "equipo_revocado"))
        self.assertEqual(self.post(adm, csrf_a, f"/api/equipos/{eq['id']}/reactivar").status_code, 204)
        c3, csrf3 = self.entrar("supervisor")
        self.assertEqual(self.registrar(c3, csrf3, id=eq["id"], secreto=eq["secreto"]).status_code, 200)

    def test_solo_el_administrador_ve_todos(self):
        c, _ = self.entrar("supervisor")
        self.assertEqual(c.get("/api/equipos").status_code, 403)
