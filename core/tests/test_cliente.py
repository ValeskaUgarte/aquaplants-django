"""Cliente particular: compra un equipo, se registra con su clave de activación y recibe sus propias alertas y servicio técnico."""
import json

from django.test import Client

from core import repo
from core.management.commands.usuarios_demo import CLAVE_ACTIVACION_DEMO, CODIGO_EQUIPO_VENTA, asegurar_equipo_venta_demo
from core.tests.base import Base

PW = "Cliente-Prueba-2026!x"


class ClienteParticular(Base):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(asegurar_equipo_venta_demo())
        self.adm, self.csrf_a = self.entrar("administrador")

    def registrar(self, correo="ana@ejemplo.cl", clave=CLAVE_ACTIVACION_DEMO, codigo=CODIGO_EQUIPO_VENTA):
        c = Client()
        r = c.post("/api/auth/registro", json.dumps({"email": correo, "nombre": "Ana Pérez", "password": PW, "codigo_dispositivo": codigo,
                                                      "clave_activacion": clave, "nombre_cultivo": "Albahaca"}), content_type="application/json")
        return c, r

    def test_registro_entrega_el_equipo_una_sola_vez(self):
        c, r = self.registrar()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["usuario"]["rol"], "cliente")
        self.assertEqual(c.get("/api/auth/me").status_code, 200)
        _, r2 = self.registrar("otra@ejemplo.cl")                      # la clave es de un solo uso
        self.assertEqual(r2.status_code, 400)
        _, r3 = self.registrar("mala@ejemplo.cl", clave="AAAA-BBBB-CCCC")
        self.assertEqual(r3.status_code, 400)

    def test_el_cliente_solo_ve_lo_suyo_y_el_supervisor_no_lo_ve(self):
        c, _ = self.registrar()
        mias = c.get("/api/instalaciones").json()
        self.assertEqual(len(mias), 1)
        self.assertEqual([d["codigo"] for d in c.get("/api/dispositivos").json()], [CODIGO_EQUIPO_VENTA])
        sup, _ = self.entrar("supervisor")
        self.assertNotIn(mias[0]["id"], [i["id"] for i in sup.get("/api/instalaciones").json()])
        self.assertNotIn(CODIGO_EQUIPO_VENTA, [d["codigo"] for d in sup.get("/api/dispositivos").json()])

    def test_solicita_tecnico_a_la_bolsa_y_puede_cancelar(self):
        c, _ = self.registrar()
        csrf = c.get("/api/auth/me").json()["csrfToken"]
        disp = c.get("/api/dispositivos").json()[0]
        r = self.post(c, csrf, "/api/mantenciones", {"dispositivo_id": disp["id"], "tipo": "tecnico", "accion": "mantencion", "nota": "Revisión preventiva del equipo"})
        self.assertEqual(r.status_code, 201, r.content)
        r2 = self.post(c, csrf, "/api/mantenciones", {"dispositivo_id": disp["id"], "tipo": "tecnico", "nota": "Quiero este técnico", "tecnico_id": "x"})
        self.assertEqual(r2.status_code, 201)                      # el técnico que pida se ignora: va a la bolsa común
        tec, _ = self.entrar("tecnico")
        lista = tec.get("/api/mantenciones").json()
        self.assertEqual(len(lista), 2)
        self.assertTrue(all(not m.get("tecnico_id") for m in lista))
        mid = r.json().get("id") or lista[0]["id"]
        self.assertEqual(self.patch(c, csrf, f"/api/mantenciones/{mid}", {"estado": "cancelada", "nota": "Ya no hace falta"}).status_code, 204)
        sup, _ = self.entrar("supervisor")
        self.assertEqual(sup.get("/api/mantenciones").json(), [])

    def test_clave_de_activacion_solo_la_genera_el_administrador(self):
        did = next(d["id"] for d in self.adm.get("/api/dispositivos").json() if d["codigo"] == CODIGO_EQUIPO_VENTA)
        r = self.post(self.adm, self.csrf_a, f"/api/dispositivos/{did}/clave-activacion")
        self.assertEqual(r.status_code, 200, r.content)
        c, _ = self.registrar("luis@ejemplo.cl", clave=r.json()["clave"])
        self.assertEqual(_.status_code, 201)
        self.assertEqual(self.post(self.adm, self.csrf_a, f"/api/dispositivos/{did}/clave-activacion").status_code, 409)
        enc, csrf = self.entrar("encargado_cultivo")
        self.assertEqual(self.post(enc, csrf, f"/api/dispositivos/{did}/clave-activacion").status_code, 403)

    def test_cliente_no_puede_ser_creado_sin_registro_con_equipo(self):
        c = Client()
        r = c.post("/api/auth/registro", json.dumps({"email": "x@ejemplo.cl", "nombre": "X Y", "password": PW}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
