"""Servicio técnico, usuarios (suspender, eliminar, vincular), ubicación GPS y estadísticas."""
import io
import json

from django.test import Client
from openpyxl import load_workbook

from core import notificaciones, repo
from core.rtdb import rtdb
from core.tests.base import Base

PW = "Tecnico-Prueba-2026!x"


class TecnicoBase(Base):
    def setUp(self):
        super().setUp()
        self.adm, self.csrf_a = self.entrar("administrador")
        self.enc, self.csrf_e = self.entrar("encargado_cultivo")
        self.tec, self.csrf_t = self.entrar("tecnico")
        self.tec_id = next(u["id"] for u in self.adm.get("/api/usuarios").json() if u["rol"] == "tecnico")

    def nuevo_tecnico(self, correo="tecnico2@aquaplants.demo"):
        r = self.post(self.adm, self.csrf_a, "/api/usuarios", {"email": correo, "nombre": "Técnico Dos", "password": PW, "rol": "tecnico"})
        self.assertEqual(r.status_code, 201, r.content)
        c = Client()
        l = c.post("/api/auth/login", json.dumps({"email": correo, "password": PW}), content_type="application/json")
        self.assertEqual(l.status_code, 200, l.content)
        return r.json()["id"], c, l.json()["csrfToken"]

    def alerta(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")])
        return self.adm.get("/api/alertas").json()[0]["id"]

    def avisos(self, c):
        return c.get("/api/notificaciones").json()["items"]


class SolicitudesTecnicasTests(TecnicoBase):
    def test_conexion_directa_con_el_tecnico_asignado_y_cierre_por_el_tecnico(self):
        iid = self.adm.get("/api/instalaciones").json()[0]["id"]
        self.assertEqual(self.patch(self.adm, self.csrf_a, f"/api/instalaciones/{iid}", {"tecnico_id": self.tec_id}).status_code, 204)
        self.assertEqual(self.patch(self.adm, self.csrf_a, f"/api/instalaciones/{iid}", {"tecnico_id": "no-existe"}).status_code, 400)
        otro_id, otro, _ = self.nuevo_tecnico()
        aid = self.alerta()
        r = self.post(self.enc, self.csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "dispositivo", "accion": "reemplazo", "nota": "No enciende, hay que cambiarlo"})
        self.assertEqual(r.status_code, 201, r.content)
        mid = r.json()["id"]
        # le llega solo al técnico asignado
        m = self.tec.get("/api/mantenciones").json()
        self.assertEqual((len(m), m[0]["accion"], m[0]["tipo"], m[0]["solicitante"]["nombre"], m[0]["dispositivo"]), (1, "reemplazo", "dispositivo", "Encargado Demo", "AQB-01"))
        self.assertIn("Nueva solicitud técnica", [n["titulo"] for n in self.avisos(self.tec)])
        self.assertEqual(otro.get("/api/mantenciones").json(), [])
        self.assertNotIn("Nueva solicitud técnica", [n["titulo"] for n in self.avisos(otro)])
        # el técnico solo ve lo suyo: nada de alertas, instalaciones ni usuarios
        self.assertEqual(self.tec.get("/api/alertas").json(), [])
        self.assertEqual(self.tec.get("/api/instalaciones").json(), [])
        self.assertEqual(self.tec.get("/api/usuarios").status_code, 403)
        self.assertEqual(self.tec.get("/api/informes", {"tipo": "alertas"}).status_code, 403)
        # flujo del técnico: aceptar, no poder cerrar sin decir cómo quedó, cerrar
        self.assertEqual(self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "en_curso"}).status_code, 204)
        self.assertEqual(self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "completada", "nota": "Se cambió el equipo"}).status_code, 400)
        self.assertEqual(self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "cancelada", "nota": "No corresponde"}).status_code, 403)
        self.assertEqual(self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "completada", "nota": "Se cambió el equipo", "resolucion": "reemplazado", "repuestos": "Placa nueva"}).status_code, 204)
        hecho = self.enc.get("/api/mantenciones", {"estado": "completada"}).json()[0]
        self.assertEqual((hecho["resolucion"], hecho["repuestos"], hecho["completada_por"]), ("reemplazado", "Placa nueva", "Técnico Demo (servicio técnico)"))
        self.assertIn("Solicitud técnica actualizada", [n["titulo"] for n in self.avisos(self.enc)])
        self.assertEqual(self.adm.get("/api/alertas").json()[0]["estado"], "activa")        # la alerta se cierra sola cuando el dato vuelve a la normalidad

    def test_bolsa_de_solicitudes_aceptar_y_devolver(self):
        otro_id, otro, csrf_o = self.nuevo_tecnico()
        aid = self.alerta()
        mid = self.post(self.enc, self.csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "bateria", "nota": "Parece la batería"}).json()["id"]
        self.assertEqual((len(self.tec.get("/api/mantenciones").json()), len(otro.get("/api/mantenciones").json())), (1, 1))     # sin técnico asignado: llega a todos
        self.assertTrue(self.tec.get("/api/mantenciones").json()[0]["en_bolsa"])
        self.assertEqual(self.patch(otro, csrf_o, f"/api/mantenciones/{mid}", {"estado": "en_curso"}).status_code, 204)                  # el primero se la queda
        self.assertEqual(self.tec.get("/api/mantenciones").json(), [])
        self.assertEqual(self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "en_curso"}).status_code, 404)
        self.assertEqual(self.patch(otro, csrf_o, f"/api/mantenciones/{mid}", {"estado": "devuelta"}).status_code, 400)                  # hay que explicar por qué
        self.assertEqual(self.patch(otro, csrf_o, f"/api/mantenciones/{mid}", {"estado": "devuelta", "nota": "No cubro esa zona"}).status_code, 204)
        self.assertEqual(len(self.tec.get("/api/mantenciones").json()), 1)                                                              # de vuelta en la bolsa
        # el supervisor/administrador puede asignarla directamente
        self.assertEqual(self.patch(self.adm, self.csrf_a, f"/api/mantenciones/{mid}", {"tecnico_id": self.tec_id}).status_code, 204)
        self.assertFalse(self.tec.get("/api/mantenciones").json()[0]["en_bolsa"])
        self.assertEqual(otro.get("/api/mantenciones").json(), [])

    def test_solicitud_directa_sin_alerta_para_retiro_o_mantencion(self):
        did = self.adm.get("/api/dispositivos").json()[0]["id"]
        r = self.post(self.enc, self.csrf_e, "/api/mantenciones", {"dispositivo_id": did, "tipo": "otro", "accion": "retiro", "nota": "Nos mudamos, hay que retirar el equipo"})
        self.assertEqual(r.status_code, 201, r.content)
        m = self.tec.get("/api/mantenciones").json()
        self.assertEqual((m[0]["accion"], m[0]["alerta_id"]), ("retiro", None))
        self.assertEqual(self.post(self.tec, self.csrf_t, "/api/mantenciones", {"dispositivo_id": did, "tipo": "otro", "nota": "no debe poder"}).status_code, 403)
        self.assertEqual(len(self.enc.get("/api/tecnicos").json()), 1)
        self.assertEqual(self.tec.get("/api/tecnicos").status_code, 403)


class UsuariosTests(TecnicoBase):
    def test_vincular_suspender_eliminar(self):
        disp = self.adm.get("/api/dispositivos").json()
        sur = next(d for d in disp if d["instalacion_codigo"] == "INV-SUR")
        r = self.post(self.adm, self.csrf_a, "/api/usuarios", {"email": "hogar@aquaplants.demo", "nombre": "Dueña del hogar", "password": PW, "rol": "encargado_cultivo",
                                                               "dispositivos": [sur["id"]]})
        self.assertEqual(r.status_code, 201, r.content)
        uid = r.json()["id"]
        u = next(x for x in self.adm.get("/api/usuarios").json() if x["id"] == uid)
        self.assertEqual(u["dispositivos"], [sur["id"]])
        self.assertEqual(u["instalaciones"], [sur["instalacion_id"]])                  # vincular el dispositivo le da acceso a su instalación
        self.assertEqual(self.post(self.adm, self.csrf_a, "/api/usuarios", {"email": "x@aquaplants.demo", "nombre": "Mala", "password": PW, "rol": "encargado_cultivo", "dispositivos": ["no-existe"]}).status_code, 400)
        # con un responsable asignado al dispositivo, el aviso va a él y no a todos los encargados de la instalación
        dest = notificaciones._destinatarios(sur["instalacion_id"], encargados=True, supervisores=False, administradores=False, dispositivo_id=sur["id"])
        self.assertEqual(dest, [uid])
        self.assertEqual(next(d for d in self.adm.get("/api/dispositivos").json() if d["id"] == sur["id"])["responsables"][0]["nombre"], "Dueña del hogar")
        # suspender: no puede entrar, queda el motivo; reactivar: vuelve a entrar
        self.assertEqual(self.patch(self.adm, self.csrf_a, f"/api/usuarios/{uid}", {"activo": False, "suspension_motivo": "Vacaciones"}).status_code, 204)
        u = next(x for x in self.adm.get("/api/usuarios").json() if x["id"] == uid)
        self.assertEqual((u["activo"], u["suspension"]["motivo"], u["suspension"]["por"]), (False, "Vacaciones", "Administrador Demo"))
        login = lambda: Client().post("/api/auth/login", json.dumps({"email": "hogar@aquaplants.demo", "password": PW}), content_type="application/json").status_code  # noqa: E731
        self.assertNotEqual(login(), 200)
        self.assertEqual(self.patch(self.adm, self.csrf_a, f"/api/usuarios/{uid}", {"activo": True}).status_code, 204)
        self.assertEqual(login(), 200)
        # eliminar: no entra más, desaparece de la lista, el correo queda libre y no puede eliminarse a sí mismo
        yo = next(x["id"] for x in self.adm.get("/api/usuarios").json() if x["rol"] == "administrador")
        self.assertEqual(self.enviar_api(self.adm, self.csrf_a, "delete", f"/api/usuarios/{yo}").status_code, 409)
        self.assertEqual(self.enviar_api(self.adm, self.csrf_a, "delete", f"/api/usuarios/{uid}").status_code, 204)
        self.assertNotEqual(login(), 200)
        self.assertNotIn(uid, [x["id"] for x in self.adm.get("/api/usuarios").json()])
        self.assertEqual(self.post(self.adm, self.csrf_a, "/api/usuarios", {"email": "hogar@aquaplants.demo", "nombre": "Otra persona", "password": PW, "rol": "encargado_cultivo"}).status_code, 201)
        self.assertEqual(rtdb().get(f"usuarios/{uid}/nombre"), "Dueña del hogar")           # el historial conserva su nombre

    def test_eliminar_un_tecnico_devuelve_sus_solicitudes_a_la_bolsa(self):
        aid = self.alerta()
        mid = self.post(self.enc, self.csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "otro", "nota": "Revisar el equipo"}).json()["id"]
        self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "en_curso"})
        self.assertEqual(self.enviar_api(self.adm, self.csrf_a, "delete", f"/api/usuarios/{self.tec_id}").status_code, 204)
        m = self.adm.get("/api/mantenciones").json()[0]
        self.assertEqual((m["estado"], m["en_bolsa"]), ("pendiente", True))


class UbicacionYEstadisticasTests(TecnicoBase):
    def test_gps_historial_movimiento_y_correccion_manual(self):
        g1 = {"lat": -33.4489, "lng": -70.6693, "precision": 4.5}
        self.assertEqual(self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")], extra={"gps": g1}).status_code, 200)
        u = next(d for d in self.adm.get("/api/ubicaciones").json() if d["codigo"] == "AQB-01")
        self.assertEqual((u["ubicacion"]["lat"], u["ubicacion"]["lng"], u["ubicacion"]["origen"]), (-33.4489, -70.6693, "gps"))
        self.assertEqual(self.enviar([self.med("CAU1", "caudal", 2.0, "L/min", ms=None)], extra={"gps": {"lat": 95, "lng": 0}}).status_code, 400)   # coordenadas imposibles
        # el dispositivo se mueve ~220 m: queda en el historial y como evento
        self.enviar([self.med("CAU1", "caudal", 2.1, "L/min")], extra={"gps": {"lat": -33.4469, "lng": -70.6693}})
        did = u["id"]
        t = self.adm.get(f"/api/dispositivos/{did}/trayecto").json()
        self.assertEqual(len(t["puntos"]), 2)
        self.assertTrue(any(e.get("tipo") == "dispositivo_movido" for _, e in rtdb().rango("eventos", ultimos=50)))
        # corrección manual: supervisor/administrador sí, encargado y técnico no; coordenadas inválidas no
        sup, csrf_s = self.entrar("supervisor")
        self.assertEqual(self.patch(sup, csrf_s, f"/api/dispositivos/{did}/ubicacion", {"lat": -33.0, "lng": -70.0}).status_code, 204)
        self.assertEqual(next(d for d in sup.get("/api/ubicaciones").json() if d["id"] == did)["ubicacion"]["origen"], "manual")
        self.assertEqual(self.patch(sup, csrf_s, f"/api/dispositivos/{did}/ubicacion", {"lat": 120, "lng": 0}).status_code, 400)
        self.assertEqual(self.patch(self.enc, self.csrf_e, f"/api/dispositivos/{did}/ubicacion", {"lat": -33.0, "lng": -70.0}).status_code, 403)
        # el técnico solo ve la ubicación de los equipos de sus solicitudes
        self.assertEqual(self.tec.get("/api/ubicaciones").json(), [])
        aid = self.alerta()
        self.post(self.enc, self.csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "dispositivo", "nota": "Falla del dispositivo"})
        self.assertEqual([d["codigo"] for d in self.tec.get("/api/ubicaciones").json()], ["AQB-01"])
        self.assertEqual(self.tec.get(f"/api/dispositivos/{did}/trayecto").status_code, 200)

    def test_clave_de_google_maps_y_csp(self):
        self.assertEqual(self.adm.get("/api/config-mapa").json(), {"google_key": ""})
        self.assertNotIn("maps.googleapis.com", self.adm.get("/api/config-mapa")["Content-Security-Policy"])
        self.assertEqual(self.enviar_api(self.adm, self.csrf_a, "put", "/api/parametros/google_maps_api_key", {"valor": "AIzaSyPrueba_123-abc"}).status_code, 204)
        self.assertEqual(self.enc.get("/api/config-mapa").json(), {"google_key": "AIzaSyPrueba_123-abc"})
        self.assertIn("https://maps.googleapis.com", self.adm.get("/api/config-mapa")["Content-Security-Policy"])
        self.assertEqual(self.enviar_api(self.adm, self.csrf_a, "put", "/api/parametros/google_maps_api_key", {"valor": "mala clave <script>"}).status_code, 400)

    def test_estadisticas_e_informe_de_mantenciones(self):
        aid = self.alerta()
        mid = self.post(self.enc, self.csrf_e, f"/api/alertas/{aid}/derivar", {"tipo": "bateria", "nota": "Parece la batería"}).json()["id"]
        self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "en_curso"})
        self.patch(self.tec, self.csrf_t, f"/api/mantenciones/{mid}", {"estado": "completada", "nota": "Batería nueva", "resolucion": "arreglado"})
        e = self.adm.get("/api/estadisticas", {"dias": 7}).json()
        self.assertEqual((e["kpis"]["alertas"], e["kpis"]["abiertas"], len(e["serie"]), e["dispositivos"]["total"]), (1, 1, 7, 4))
        self.assertEqual((e["mantenciones"]["total"], e["mantenciones"]["completadas"], e["mantenciones"]["por_resolucion"][0]["nombre"]), (1, 1, "Arreglado"))
        self.assertEqual(self.tec.get("/api/estadisticas").json()["mantenciones"]["completadas"], 1)
        self.assertNotIn("kpis", self.tec.get("/api/estadisticas").json())
        r = self.adm.get("/api/informes", {"tipo": "mantenciones"})
        self.assertEqual(r.status_code, 200, r.content)
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Resumen", "Mantenciones", "Técnicos", "Dispositivos y ubicación"])
        tec = list(wb["Técnicos"].iter_rows(values_only=True))
        self.assertEqual((tec[1][0], tec[1][1], tec[1][3], tec[1][9]), ("Técnico Demo (servicio técnico)", 1, 1, 1))
