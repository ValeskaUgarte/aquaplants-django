import json
import socket
import time

from django.test import Client

from core import descubrimiento, monitor, repo, seguridad
from core.rtdb import rtdb
from core.tests.base import Base

CAU = ("CAU1", "caudal", "L/min")
NUT = ("NUT1", "nutrientes", "ppm")


class AutenticacionTests(Base):
    def test_login_me_y_logout(self):
        c, csrf = self.entrar()
        u = c.get("/api/auth/me").json()
        self.assertEqual((u["usuario"]["rol"], u["csrfToken"]), ("administrador", csrf))
        self.assertEqual(self.post(c, csrf, "/api/auth/logout").status_code, 204)
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_bloqueo_por_intentos_fallidos_y_mismo_error_si_no_existe(self):
        c = Client()
        for _ in range(5):
            r = c.post("/api/auth/login", json.dumps({"email": self.correo["supervisor"], "password": "incorrecta"}), content_type="application/json")
            self.assertEqual(r.status_code, 401)
        r = c.post("/api/auth/login", json.dumps({"email": self.correo["supervisor"], "password": self.pw["supervisor"]}), content_type="application/json")
        self.assertEqual(r.status_code, 423)                    # bloqueada aunque la clave sea correcta
        r = Client().post("/api/auth/login", json.dumps({"email": "nadie@x.cl", "password": "x"}), content_type="application/json")
        self.assertEqual((r.status_code, r.json()["error"]["codigo"]), (401, "credenciales_invalidas"))

    def test_post_exige_csrf_y_sesion(self):
        c, _ = self.entrar()
        self.assertEqual(c.post("/api/auth/logout", "{}", content_type="application/json").status_code, 403)
        self.assertEqual(Client().get("/api/panel").status_code, 401)

    def test_secretos_no_quedan_en_claro(self):
        usuarios = rtdb().get("usuarios")
        self.assertTrue(all(u["hash"].startswith("scrypt$") for u in usuarios.values()))
        self.assertNotIn(self.pw["administrador"], json.dumps(usuarios))
        self.assertNotIn(self.disp[0]["secreto"], json.dumps(rtdb().get("dispositivos")))

    def test_politica_de_contrasena_y_revocacion_de_otras_sesiones(self):
        c, csrf = self.entrar("supervisor")
        otra, _ = self.entrar("supervisor")
        debil = self.post(c, csrf, "/api/auth/password", {"actual": self.pw["supervisor"], "nueva": "corta123"})
        self.assertEqual(debil.status_code, 400)
        self.assertTrue(debil.json()["error"]["detalles"])
        self.assertEqual(self.post(c, csrf, "/api/auth/password", {"actual": "mala", "nueva": "Nueva-Clave#2026x"}).status_code, 403)
        self.assertEqual(self.post(c, csrf, "/api/auth/password", {"actual": self.pw["supervisor"], "nueva": "Nueva-Clave#2026x"}).status_code, 204)
        self.assertEqual(otra.get("/api/auth/me").status_code, 401)        # la otra sesión se cerró
        self.assertEqual(c.get("/api/auth/me").status_code, 200)           # la actual sigue

    def test_segundo_factor_completo(self):
        c, csrf = self.entrar("supervisor")
        cfg = self.post(c, csrf, "/api/auth/2fa/iniciar").json()
        self.assertTrue(cfg["uri"].startswith("otpauth://totp/"))
        self.assertEqual(self.post(c, csrf, "/api/auth/2fa/activar", {"codigo": "000000"}).status_code, 400)
        self.assertEqual(self.post(c, csrf, "/api/auth/2fa/activar", {"codigo": seguridad.codigo_totp(cfg["secreto"])}).status_code, 204)
        self.assertTrue(c.get("/api/auth/me").json()["usuario"]["totpActivo"])
        nuevo = Client()
        cred = {"email": self.correo["supervisor"], "password": self.pw["supervisor"]}
        r = nuevo.post("/api/auth/login", json.dumps(cred), content_type="application/json")
        self.assertEqual((r.status_code, r.json()["error"]["codigo"]), (401, "totp_requerido"))
        malo = nuevo.post("/api/auth/login", json.dumps({**cred, "codigo_totp": "123456"}), content_type="application/json")
        self.assertEqual(malo.status_code, 401)
        bueno = nuevo.post("/api/auth/login", json.dumps({**cred, "codigo_totp": seguridad.codigo_totp(cfg["secreto"])}), content_type="application/json")
        self.assertEqual(bueno.status_code, 200)
        csrf2 = bueno.json()["csrfToken"]
        self.assertEqual(self.post(nuevo, csrf2, "/api/auth/2fa/desactivar", {"password": self.pw["supervisor"], "codigo": seguridad.codigo_totp(cfg["secreto"])}).status_code, 204)

    def test_acepta_cualquier_host_en_desarrollo(self):
        c = Client(HTTP_HOST="192.168.1.45:8000")
        r = c.get("/api/salud")
        self.assertEqual((r.status_code, r.json()["app"]), (200, "aquaplants"))


class DatosDeDemostracionTests(Base):
    def test_cada_cultivo_tiene_su_dispositivo_y_sus_niveles(self):
        c, _ = self.entrar()
        disp = self.ids(c, "dispositivos")
        self.assertEqual(sorted(d["cultivo_nombre"] for d in disp), ["Albahaca", "Frutilla", "Lechuga", "Tomate cherry"])
        self.assertEqual(len({d["cultivo_id"] for d in disp}), 4)                  # un dispositivo por cultivo
        # niveles distintos por cultivo: el tomate usa más nutrientes que la lechuga
        nut = {x["cultivo"]: x["valor"] for i in self.ids(c, "instalaciones") for x in c.get("/api/estado-actual", {"instalacion_id": i["id"]}).json()
               if x["tipo_variable"] == "nutrientes"}
        self.assertGreater(nut["Tomate cherry"], 1.5 * nut["Lechuga"])
        reglas = self.ids(c, "reglas-alerta")
        self.assertTrue(all(r["sensor_id"] for r in reglas))                      # reglas propias de cada cultivo
        self.assertTrue(any(r["nombre"].startswith("Tomate cherry:") for r in reglas))

    def test_estado_actual_dice_cultivo_y_dispositivo(self):
        c, _ = self.entrar()
        inst = next(i["id"] for i in self.ids(c, "instalaciones") if i["codigo"] == "INV-NORTE")
        filas = c.get("/api/estado-actual", {"instalacion_id": inst}).json()
        self.assertEqual({(f["cultivo"], f["dispositivo_codigo"]) for f in filas}, {("Lechuga", "AQB-01"), ("Albahaca", "AQB-02")})
        self.assertTrue(all(f["valor"] is not None for f in filas))

    def test_panel_lista_los_cultivos_de_cada_instalacion(self):
        c, _ = self.entrar()
        d = c.get("/api/panel").json()
        self.assertEqual(d["totales"]["dispositivos"], 4)
        self.assertGreater(d["totales"]["mediciones_24h"], 2000)
        norte = next(i for i in d["instalaciones"] if i["codigo"] == "INV-NORTE")
        self.assertEqual({(x["nombre"], x["dispositivo_codigo"]) for x in norte["cultivos"]}, {("Lechuga", "AQB-01"), ("Albahaca", "AQB-02")})


class RolesTests(Base):
    def test_encargado_solo_ve_su_instalacion(self):
        c, _ = self.entrar("encargado_cultivo")
        p = c.get("/api/panel").json()
        self.assertEqual([i["codigo"] for i in p["instalaciones"]], ["INV-NORTE"])
        admin, _ = self.entrar()
        sur = next(i["id"] for i in self.ids(admin, "instalaciones") if i["codigo"] == "INV-SUR")
        self.assertEqual(c.get("/api/estado-actual", {"instalacion_id": sur}).status_code, 404)
        self.assertEqual(c.get("/api/mediciones", {"sensor_id": self.sensor_id(admin, "Tomate cherry", "caudal")}).status_code, 404)
        self.assertEqual(c.get("/api/comparacion", {"tipo_variable": "caudal"}).status_code, 403)
        self.assertEqual(c.get("/api/auditoria").status_code, 403)
        self.assertEqual([x["nombre"] for x in c.get("/api/cultivos").json()], ["Albahaca", "Lechuga"])

    def test_supervisor_ve_todo_compara_y_no_administra(self):
        c, csrf = self.entrar("supervisor")
        self.assertEqual(len(c.get("/api/panel").json()["instalaciones"]), 2)
        self.assertEqual(c.get("/api/comparacion", {"tipo_variable": "caudal"}).status_code, 200)
        self.assertEqual(c.get("/api/usuarios").status_code, 403)
        self.assertEqual(self.post(c, csrf, "/api/reglas-alerta", {}).status_code, 403)
        self.assertEqual(self.post(c, csrf, "/api/alertas/abc/reconocer").status_code, 404)   # puede reconocer (cubre al encargado); esa alerta no existe
        self.assertEqual(c.get("/api/simulacion").status_code, 403)

    def test_admin_gestiona_usuarios_con_validaciones(self):
        c, csrf = self.entrar()
        sur = next(i["id"] for i in self.ids(c, "instalaciones") if i["codigo"] == "INV-SUR")
        ok = {"email": "nuevo@aquaplants.demo", "nombre": "Persona Nueva", "rol": "encargado_cultivo", "password": "Clave-Segura#2026x", "instalaciones": [sur]}
        self.assertEqual(self.post(c, csrf, "/api/usuarios", ok).status_code, 201)
        self.assertEqual(self.post(c, csrf, "/api/usuarios", ok).status_code, 409)
        self.assertEqual(self.post(c, csrf, "/api/usuarios", {**ok, "email": "o@x.cl", "password": "corta"}).status_code, 400)
        self.assertEqual(self.post(c, csrf, "/api/usuarios", {**ok, "email": "o@x.cl", "instalaciones": ["noexiste"]}).status_code, 400)
        self.assertEqual(self.post(c, csrf, "/api/usuarios", {**ok, "email": "o@x.cl", "campo_raro": 1}).status_code, 400)
        nuevo = Client()
        r = nuevo.post("/api/auth/login", json.dumps({"email": ok["email"], "password": ok["password"]}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i["codigo"] for i in nuevo.get("/api/panel").json()["instalaciones"]], ["INV-SUR"])

    def test_desactivar_cierra_sesiones_y_no_puede_desactivarse_a_si_mismo(self):
        c, csrf = self.entrar()
        yo = next(u["id"] for u in c.get("/api/usuarios").json() if u["email"] == self.correo["administrador"])
        self.assertEqual(self.patch(c, csrf, f"/api/usuarios/{yo}", {"activo": False}).status_code, 409)
        sup, _ = self.entrar("supervisor")
        otro = next(u["id"] for u in c.get("/api/usuarios").json() if u["email"] == self.correo["supervisor"])
        self.assertEqual(self.patch(c, csrf, f"/api/usuarios/{otro}", {"activo": False}).status_code, 204)
        self.assertEqual(sup.get("/api/auth/me").status_code, 401)


class CatalogoTests(Base):
    def test_un_dispositivo_por_cultivo(self):
        c, csrf = self.entrar()
        lechuga = next(x for x in self.ids(c, "cultivos") if x["nombre"] == "Lechuga")
        r = self.post(c, csrf, "/api/dispositivos", {"instalacion_id": lechuga["instalacion_id"], "cultivo_id": lechuga["id"], "codigo": "AQB-99", "nombre": "Otro"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("un dispositivo por cultivo", r.json()["error"]["mensaje"])
        nuevo = self.post(c, csrf, "/api/cultivos", {"instalacion_id": lechuga["instalacion_id"], "nombre": "Rúcula"})
        self.assertEqual(nuevo.status_code, 201)
        cid = nuevo.json()["id"]
        r = self.post(c, csrf, "/api/dispositivos", {"instalacion_id": lechuga["instalacion_id"], "cultivo_id": cid, "codigo": "AQB-05", "nombre": "AquaBrain · Rúcula"})
        self.assertEqual(r.status_code, 201)
        self.assertIn("secreto", r.json())
        self.assertEqual(self.post(c, csrf, "/api/dispositivos", {"instalacion_id": lechuga["instalacion_id"], "cultivo_id": cid, "codigo": "AQB-06", "nombre": "Otro AquaBrain"}).status_code, 409)

    def test_cultivo_de_otra_instalacion_se_rechaza_y_sensor_hereda_cultivo(self):
        c, csrf = self.entrar()
        lechuga, tomate = (next(x for x in self.ids(c, "cultivos") if x["nombre"] == n) for n in ("Lechuga", "Tomate cherry"))
        r = self.post(c, csrf, "/api/dispositivos", {"instalacion_id": lechuga["instalacion_id"], "cultivo_id": tomate["id"], "codigo": "AQB-07", "nombre": "Otro AquaBrain"})
        self.assertEqual(r.status_code, 400)
        did = next(d["id"] for d in self.ids(c, "dispositivos") if d["codigo"] == "AQB-03")
        s = self.post(c, csrf, "/api/sensores", {"dispositivo_id": did, "tipo_sensor_id": "caudal", "codigo": "CAU2", "nombre": "Caudal extra"})
        self.assertEqual(s.status_code, 201)
        creado = next(x for x in self.ids(c, "sensores") if x["id"] == s.json()["id"])
        self.assertEqual(creado["cultivo_nombre"], "Tomate cherry")                  # el sensor mide el cultivo de su dispositivo

    def test_eliminar_cultivo_con_dispositivo_se_bloquea_y_sin_dispositivo_borra_plantas(self):
        c, csrf = self.entrar()
        lechuga = self.cultivo_id(c, "Lechuga")
        self.assertEqual(c.delete(f"/api/cultivos/{lechuga}", HTTP_X_CSRF_TOKEN=csrf).status_code, 409)
        libre = self.post(c, csrf, "/api/cultivos", {"instalacion_id": next(x["instalacion_id"] for x in self.ids(c, "cultivos") if x["id"] == lechuga), "nombre": "Espinaca"}).json()["id"]
        self.assertEqual(self.post(c, csrf, "/api/plantas", {"cultivo_id": libre, "codigo": "E1", "nombre": "Espinaca 1"}).status_code, 201)
        self.assertEqual(c.delete(f"/api/cultivos/{libre}", HTTP_X_CSRF_TOKEN=csrf).status_code, 204)
        self.assertFalse([p for p in self.ids(c, "plantas") if p["cultivo_id"] == libre])

    def test_reglas_y_parametros(self):
        c, csrf = self.entrar()
        sid = self.sensor_id(c, "Lechuga", "nutrientes")
        self.assertEqual(self.post(c, csrf, "/api/reglas-alerta", {"nombre": "Doble", "sensor_id": sid, "tipo_sensor_id": "nutrientes", "operador": ">", "umbral": 1}).status_code, 400)
        self.assertEqual(self.post(c, csrf, "/api/reglas-alerta", {"nombre": "Ninguno", "operador": ">", "umbral": 1}).status_code, 400)
        self.assertEqual(self.post(c, csrf, "/api/reglas-alerta", {"nombre": "Nutrientes tope", "sensor_id": sid, "operador": ">=", "umbral": 900}).status_code, 201)
        p = c.get("/api/parametros").json()
        self.assertEqual({x["clave"] for x in p}, {"minutos_sin_comunicacion", "retencion_historicos_dias", "google_maps_api_key"})
        r = c.put("/api/parametros/minutos_sin_comunicacion", json.dumps({"valor": "10"}), content_type="application/json", HTTP_X_CSRF_TOKEN=csrf)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(repo.parametro_int("minutos_sin_comunicacion", 5), 10)

    def test_tipos_de_sensor_nuevos(self):
        c, csrf = self.entrar()
        r = self.post(c, csrf, "/api/tipos-sensor", {"codigo": "turbidez", "nombre": "Turbidez del agua", "tipo_variable": "turbidez", "unidad": "NTU", "valor_min": 0, "valor_max": 1000})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.post(c, csrf, "/api/tipos-sensor", {"codigo": "turbidez", "nombre": "Turbidez repetida", "tipo_variable": "turbidez", "unidad": "NTU"}).status_code, 409)


class IngestaTests(Base):
    def test_lote_valido_actualiza_estado_historial_y_contador(self):
        antes = self.entrar()[0].get("/api/panel").json()["totales"]["mediciones_24h"]
        r = self.enviar([self.med(*CAU[:1], CAU[1], 2.1, CAU[2]), self.med(*NUT[:1], NUT[1], 720, NUT[2])])
        self.assertEqual((r.status_code, r.json()["aceptadas"]), (200, 2))
        c, _ = self.entrar()
        inst = next(i["id"] for i in self.ids(c, "instalaciones") if i["codigo"] == "INV-NORTE")
        fila = next(f for f in c.get("/api/estado-actual", {"instalacion_id": inst}).json() if f["codigo"] == "CAU1" and f["cultivo"] == "Lechuga")
        self.assertEqual((fila["valor"], fila["estado_conexion"], fila["calidad_enlace"]), (2.1, "en_linea", 80))
        from core.vistas import operacion
        operacion.invalidar_micro()
        self.assertEqual(c.get("/api/panel").json()["totales"]["mediciones_24h"], antes + 2)

    def test_firma_timestamp_nonce_e_identidad(self):
        self.assertEqual(self.enviar([], firma_mala=True).status_code, 401)
        self.assertEqual(self.enviar([], ts=int(time.time()) - 4000).status_code, 401)
        self.assertEqual(self.enviar([], nonce="a" * 20).status_code, 200)
        self.assertEqual(self.enviar([], nonce="a" * 20).status_code, 401)             # nonce repetido
        self.assertEqual(self.enviar([], extra={"id_sistema": "INV-SUR"}).status_code, 403)
        self.assertEqual(self.enviar([], extra={"calidad_enlace": 500}).status_code, 400)
        self.assertEqual(self.enviar([self.med(*CAU[:1], CAU[1], 2, CAU[2])] * 501).status_code, 400)

    def test_rechazos_por_medicion_sin_descartar_el_resto(self):
        ahora = int(time.time() * 1000)
        r = self.enviar([self.med("XXX", "caudal", 1, "L/min"), self.med("CAU1", "nutrientes", 1, "L/min"), self.med("CAU1", "caudal", 1, "ppm"),
                         self.med("FOT1", "fotoperiodo", 30, "h"), self.med("CAU1", "caudal", 1, "L/min", ahora + 3_600_000),
                         self.med("CAU1", "caudal", "abc", "L/min"), {"id_sensor": "CAU1", "campo_extra": 1}, self.med("CAU1", "caudal", 2.0, "L/min")]).json()
        self.assertEqual([x["motivo"] for x in r["rechazadas"]], ["sensor_desconocido_o_inactivo", "tipo_variable_no_coincide", "unidad_no_coincide",
                                                                  "valor_fuera_de_limites_de_validacion", "timestamp_en_el_futuro", "formato_invalido", "formato_invalido"])
        self.assertEqual(r["aceptadas"], 1)
        c, _ = self.entrar()
        self.assertIn("medicion_rechazada", [e["tipo"] for e in c.get("/api/eventos").json()])

    def test_reenvio_diferido_no_duplica_y_registra_sincronizacion(self):
        ms = int(time.time() * 1000) - 60_000
        lote = [self.med("CAU1", "caudal", 2.0, "L/min", ms, "diferido")]
        self.assertEqual(self.enviar(lote).json()["aceptadas"], 1)
        r = self.enviar(lote).json()
        self.assertEqual((r["aceptadas"], r["duplicadas"]), (0, 1))
        c, _ = self.entrar()
        self.assertIn("sincronizacion_diferida", [e["tipo"] for e in c.get("/api/eventos").json()])

    def test_dato_fuera_de_orden_queda_en_historico_pero_no_cambia_el_estado(self):
        ahora = int(time.time() * 1000)
        self.enviar([self.med("CAU1", "caudal", 2.2, "L/min", ahora)])
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min", ahora - 120_000, "diferido")])
        c, _ = self.entrar()
        sid = self.sensor_id(c, "Lechuga", "caudal")
        self.assertEqual(rtdb().get(f"ultima/{sid}/valor"), 2.2)
        self.assertEqual(len(c.get("/api/mediciones", {"sensor_id": sid, "agregacion": "crudo"}).json()["puntos"]) > 144, True)

    def test_alerta_indica_cultivo_y_dispositivo_y_se_resuelve_sola(self):
        r = self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")]).json()      # lechuga: caudal bajo si < 1.2
        self.assertEqual(r["alertas_generadas"][0]["regla"], "Lechuga: caudal bajo")
        self.assertEqual(self.enviar([self.med("CAU1", "caudal", 0.4, "L/min")]).json()["alertas_generadas"], [])      # no duplica
        c, csrf = self.entrar()
        a = c.get("/api/alertas", {"estado": "abiertas"}).json()
        self.assertEqual(len(a), 1)
        self.assertTrue(a[0]["mensaje"].startswith("Lechuga · Caudal (CAU1)"))
        self.assertEqual((a[0]["cultivo"], a[0]["dispositivo_codigo"], a[0]["estado"]), ("Lechuga", "AQB-01", "activa"))
        self.assertEqual(self.post(c, csrf, f"/api/alertas/{a[0]['id']}/reconocer").status_code, 204)
        self.assertEqual(self.post(c, csrf, f"/api/alertas/{a[0]['id']}/reconocer").status_code, 400)       # solo las activas
        self.assertEqual(c.get("/api/alertas", {"estado": "reconocida"}).json()[0]["reconocida_por"], "Administrador Demo")
        self.enviar([self.med("CAU1", "caudal", 2.0, "L/min")])                 # volvió a lo normal: se resuelve sola, sin revisión ni confirmación
        self.assertEqual(c.get("/api/alertas", {"estado": "abiertas"}).json(), [])
        r = c.get("/api/alertas", {"estado": "resuelta"}).json()
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0]["resuelta_auto"])
        self.assertIsNotNone(r[0]["normalizada_en"])
        self.assertEqual((r[0]["bitacora"][-1]["por"], r[0]["bitacora"][-1]["accion"]), ("Sistema", "se resolvió automáticamente"))
        self.assertEqual(self.post(c, csrf, f"/api/alertas/{a[0]['id']}/revisar", {"solucionado": True, "nota": "Ya no hace falta"}).status_code, 409)

    def test_cada_cultivo_tiene_umbrales_propios(self):
        # 1.9 L/min es normal para la lechuga (base 2.0) pero está por debajo del límite del tomate (base 3.2 → bajo < 1.92)
        self.assertEqual(self.enviar([self.med("CAU1", "caudal", 1.9, "L/min")], cultivo="Lechuga").json()["alertas_generadas"], [])
        r = self.enviar([self.med("CAU1", "caudal", 1.9, "L/min")], cultivo="Tomate cherry").json()
        self.assertEqual(r["alertas_generadas"][0]["regla"], "Tomate cherry: caudal bajo")

    def test_encargado_reconoce_solo_alertas_de_su_instalacion(self):
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")], cultivo="Lechuga")
        self.enviar([self.med("CAU1", "caudal", 0.5, "L/min")], cultivo="Tomate cherry")
        admin, _ = self.entrar()
        alertas = {a["cultivo"]: a["id"] for a in admin.get("/api/alertas").json()}
        c, csrf = self.entrar("encargado_cultivo")
        self.assertEqual([a["cultivo"] for a in c.get("/api/alertas").json()], ["Lechuga"])
        self.assertEqual(self.post(c, csrf, f"/api/alertas/{alertas['Tomate cherry']}/reconocer").status_code, 404)
        self.assertEqual(self.post(c, csrf, f"/api/alertas/{alertas['Lechuga']}/reconocer").status_code, 204)

    def test_medicion_manual_y_validaciones(self):
        c, csrf = self.entrar()
        sid = self.sensor_id(c, "Albahaca", "nutrientes")
        r = self.post(c, csrf, "/api/mediciones/manual", {"sensor_id": sid, "valor": 910})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(rtdb().rango(f"mediciones/{sid}", ultimos=1)[0][1].get("o"), "manual")
        self.assertEqual(self.post(c, csrf, "/api/mediciones/manual", {"sensor_id": sid, "valor": -5}).status_code, 422)
        ts = "2026-01-01T10:00:00Z"
        self.assertEqual(self.post(c, csrf, "/api/mediciones/manual", {"sensor_id": sid, "valor": 900, "timestamp": ts}).status_code, 201)
        self.assertEqual(self.post(c, csrf, "/api/mediciones/manual", {"sensor_id": sid, "valor": 901, "timestamp": ts}).status_code, 409)
        sup, csrf2 = self.entrar("supervisor")
        self.assertEqual(self.post(sup, csrf2, "/api/mediciones/manual", {"sensor_id": sid, "valor": 1}).status_code, 403)


class MonitorYEventosTests(Base):
    def test_sin_comunicacion_alerta_con_cultivo_y_reconexion(self):
        did = next(k for k, d in rtdb().get("dispositivos").items() if d["codigo"] == "AQB-02")
        rtdb().set(f"dispositivos/{did}/ultima_comunicacion", int(time.time() * 1000) - 10 * 60_000)
        repo.invalidar("dispositivos")
        self.assertEqual(monitor.revisar_dispositivos(), 1)
        self.assertEqual(monitor.revisar_dispositivos(), 0)                              # no repite
        c, _ = self.entrar()
        a = c.get("/api/alertas", {"estado": "abiertas"}).json()
        self.assertEqual((a[0]["tipo"], a[0]["cultivo"], a[0]["dispositivo_codigo"]), ("sin_comunicacion", "Albahaca", "AQB-02"))
        self.assertIn("Albahaca", a[0]["mensaje"])
        self.assertEqual(self.enviar([], cultivo="Albahaca").status_code, 200)
        self.assertEqual(c.get("/api/alertas", {"estado": "abiertas"}).json(), [])      # volvió a comunicarse: la alerta se resolvió sola
        r = c.get("/api/alertas", {"estado": "resuelta"}).json()
        self.assertEqual((len(r), r[0]["tipo"], r[0]["resuelta_auto"]), (1, "sin_comunicacion", True))
        tipos = [e["tipo"] for e in c.get("/api/eventos").json()]
        self.assertIn("dispositivo_sin_comunicacion", tipos)
        self.assertIn("dispositivo_reconectado", tipos)


class HistoricoYComparacionTests(Base):
    def test_historico_agregado_y_crudo(self):
        c, _ = self.entrar()
        sid = self.sensor_id(c, "Frutilla", "nutrientes")
        h = c.get("/api/mediciones", {"sensor_id": sid}).json()
        self.assertTrue(0 < len(h["puntos"]) <= 400)
        self.assertEqual(set(h["puntos"][0]), {"t", "prom", "min", "max", "n"})
        crudo = c.get("/api/mediciones", {"sensor_id": sid, "agregacion": "crudo"}).json()
        self.assertGreaterEqual(len(crudo["puntos"]), 140)
        self.assertEqual(c.get("/api/mediciones", {"sensor_id": sid, "desde": "2030-01-01T00:00:00Z", "hasta": "2020-01-01T00:00:00Z"}).status_code, 400)

    def test_comparacion_por_cultivo_y_por_instalacion(self):
        c, _ = self.entrar("supervisor")
        por_cultivo = c.get("/api/comparacion", {"tipo_variable": "nutrientes", "agrupar": "cultivo"}).json()
        self.assertEqual(len(por_cultivo["instalaciones"]), 4)
        self.assertTrue(all(" · AQB-0" in i["nombre"] for i in por_cultivo["instalaciones"]))
        prom = {i["nombre"].split(" · ")[0]: i["promedio"] for i in por_cultivo["instalaciones"]}
        self.assertGreater(prom["Tomate cherry"], prom["Lechuga"])
        por_inst = c.get("/api/comparacion", {"tipo_variable": "nutrientes", "agrupar": "instalacion"}).json()
        self.assertEqual(sorted(i["nombre"] for i in por_inst["instalaciones"]), ["Invernadero Norte", "Invernadero Sur"])


class AuditoriaYSimulacionTests(Base):
    def test_auditoria_encadenada_detecta_alteraciones(self):
        c, csrf = self.entrar()
        self.post(c, csrf, "/api/instalaciones", {"codigo": "INV-ESTE", "nombre": "Invernadero Este"})
        self.assertTrue(c.get("/api/auditoria/verificar").json()["ok"])
        self.assertIn("instalacion_creada", [a["accion"] for a in c.get("/api/auditoria").json()])
        clave = rtdb().rango("auditoria", ultimos=1)[0][0]
        rtdb().set(f"auditoria/{clave}/accion", "manipulado")
        v = c.get("/api/auditoria/verificar").json()
        self.assertEqual((v["ok"], v["filaCorrupta"]), (False, clave))

    def test_simulacion_solo_admin_y_acciones_validas(self):
        sup, _ = self.entrar("supervisor")
        self.assertEqual(sup.get("/api/simulacion").status_code, 403)
        c, csrf = self.entrar()
        self.assertFalse(c.get("/api/simulacion").json()["disponible"])
        self.assertEqual(self.post(c, csrf, "/api/simulacion/accion", {"accion": "borrar_todo"}).status_code, 400)
        self.assertEqual(self.post(c, csrf, "/api/simulacion/accion", {"accion": "pausar"}).status_code, 409)


class InterfacesYAppTests(Base):
    def test_paginas_web_movil_y_consola(self):
        c = Client()
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(c.get("/movil").status_code, 200)
        self.assertEqual(c.get("/admin").status_code, 200)
        self.assertEqual(c.get("/admin/").status_code, 200)
        for js in ("comun.js", "web.js", "movil.js", "secciones_admin.js", "consola.js"):
            self.assertEqual(c.get(f"/static/{js}").status_code, 200)
        self.assertEqual(c.get("/api/no-existe").status_code, 404)

    def test_la_app_android_recibe_la_interfaz_movil(self):
        wv = "Mozilla/5.0 (Linux; Android 13; Pixel 7 Build/TQ3A; wv) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
        r = Client().get("/", HTTP_USER_AGENT=wv)
        self.assertEqual((r.status_code, r["Location"]), (302, "/movil"))
        self.assertEqual(Client().get("/", HTTP_USER_AGENT=wv, QUERY_STRING="web=1").status_code, 200)

    def test_descubrimiento_automatico_responde_a_la_app(self):
        puerto = descubrimiento.iniciar(8000, 0)
        self.assertGreater(puerto, 0)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.sendto(b"AQUAPLANTS?", ("127.0.0.1", puerto))
        self.assertEqual(s.recvfrom(100)[0], b"AQUAPLANTS 8000")
        s.close()


class CompatibilidadFirebaseTests(Base):
    """El servidor local debe comportarse como la API REST de Firebase (el cliente es el mismo)."""

    def test_consultas_por_rango_y_limites(self):
        db = rtdb()
        db.update({f"x/{i:03d}": {"v": i} for i in range(10)})
        self.assertEqual([k for k, _ in db.rango("x", ultimos=3)], ["007", "008", "009"])
        self.assertEqual([k for k, _ in db.rango("x", desde="003", hasta="005")], ["003", "004", "005"])
        self.assertEqual([k for k, _ in db.rango("x", hasta="004", primeros=2)], ["000", "001"])

    def test_nulos_borran_y_contadores_atomicos(self):
        db = rtdb()
        db.set("a/b/c", 1)
        db.update({"a/b/c": None})
        self.assertIsNone(db.get("a"))
        db.update({"n/c": {".sv": {"increment": 3}}})
        db.update({"n/c": {".sv": {"increment": 4}}})
        self.assertEqual(db.get("n/c"), 7)


class RecuperacionYErroresTests(Base):
    def _json(self, c, url, cuerpo):
        return c.post(url, json.dumps(cuerpo), content_type="application/json")

    def test_olvide_mi_contrasena_codigo_de_un_solo_uso(self):
        from unittest import mock
        from django.conf import settings
        c, mail = Client(), self.correo["supervisor"]
        antigua, nueva = self.pw["supervisor"], "Clave-Nueva-2026!ok"
        # un correo que no existe recibe EXACTAMENTE la misma respuesta (no se puede averiguar qué correos están registrados)
        r_no = self._json(c, "/api/auth/olvide", {"email": "nadie@ejemplo.cl"})
        self.assertEqual(r_no.status_code, 200)
        self.assertNotIn("codigo_demo", r_no.json())
        with mock.patch.dict(settings.AQ, {"RECUPERACION_DEMO": True}):
            r = self._json(c, "/api/auth/olvide", {"email": mail})
        self.assertEqual(r.json()["mensaje"], r_no.json()["mensaje"])
        codigo = r.json()["codigo_demo"]
        # sin el modo demostración el código nunca viaja en la respuesta
        self.assertNotIn("codigo_demo", self._json(c, "/api/auth/olvide", {"email": mail}).json())
        # contraseña débil, código falso y código correcto
        self.assertEqual(self._json(c, "/api/auth/restablecer", {"codigo": codigo, "nueva": "corta"}).status_code, 400)
        self.assertEqual(self._json(c, "/api/auth/restablecer", {"codigo": "AQ-FALSO-00000", "nueva": nueva}).status_code, 400)
        self.assertEqual(self._json(c, "/api/auth/restablecer", {"codigo": codigo.lower().replace("-", " "), "nueva": nueva}).status_code, 204)   # tolera minúsculas y separadores
        self.assertEqual(self._json(Client(), "/api/auth/login", {"email": mail, "password": antigua}).status_code, 401)
        self.assertEqual(self._json(Client(), "/api/auth/login", {"email": mail, "password": nueva}).status_code, 200)
        self.assertEqual(self._json(c, "/api/auth/restablecer", {"codigo": codigo, "nueva": "Otra-Clave-2026!xy"}).status_code, 400)           # un solo uso

    def test_codigo_vencido_no_sirve(self):
        from unittest import mock
        from django.conf import settings
        from core.vistas import auth
        c = Client()
        with mock.patch.dict(settings.AQ, {"RECUPERACION_DEMO": True}):
            codigo = self._json(c, "/api/auth/olvide", {"email": self.correo["supervisor"]}).json()["codigo_demo"]
        rtdb().update({f"recuperacion/{auth._huella_codigo(codigo)}/expira": int(time.time() * 1000) - 1000})
        self.assertEqual(self._json(c, "/api/auth/restablecer", {"codigo": codigo, "nueva": "Clave-Nueva-2026!ok"}).status_code, 400)

    def test_paginas_de_error_con_su_numero_en_el_navegador_y_json_en_la_api(self):
        c = Client()
        nav = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}
        r = c.get("/api/panel", **nav)                                   # alguien abre la API sin sesión
        self.assertEqual(r.status_code, 401)
        self.assertIn('class="error-num">401<', r.content.decode())
        r = c.get("/api/panel", HTTP_ACCEPT="application/json")          # la interfaz sigue recibiendo JSON
        self.assertEqual((r.status_code, r["Content-Type"].split(";")[0]), (401, "application/json"))
        r = c.get("/esta-direccion-no-existe", **nav)
        self.assertEqual(r.status_code, 404)
        self.assertIn('class="error-num">404<', r.content.decode())
        sup, _ = self.entrar("supervisor")
        r = sup.get("/api/usuarios", **nav)                              # con sesión pero sin permiso
        self.assertEqual(r.status_code, 403)
        self.assertIn('class="error-num">403<', r.content.decode())
