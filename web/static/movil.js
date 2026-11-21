/* App MÓVIL: operación en terreno, centrada en el CULTIVO y su DISPOSITIVO (uno por cultivo). Prioriza «¿qué necesita atención ahora?».
   Todo ocurre dentro de la app (pantallas que se apilan y se cierran con «Volver» o con el botón atrás de Android):
   nunca se abre la versión web de escritorio. En la app Android las peticiones viajan por Firebase (ver comun.js / core/relay.py). */
const raiz = document.getElementById('app');
const est = { tab: 'hoy', filtro: 'abiertas' };
let contenido, tabs, cabPunto, datos = null, conocidas = null, temporizador = null, hojaVivos = [];
const pila = [];                                             // pantallas abiertas encima de la principal
const esAdmin = () => AQ.usuario.rol === 'administrador';
const esTecnico = () => AQ.usuario.rol === 'tecnico';
const esSupervision = () => ['supervisor', 'administrador'].includes(AQ.usuario.rol);
const LECTURAS_CLAVE = ['nivel_agua', 'nutrientes', 'luz_intensidad', 'ph', 'temperatura', 'caudal', 'co2'];

function armar() {
  vaciar(raiz); pila.splice(0).forEach((h) => h.nodo.remove());
  cabPunto = el('span', { class: 'punto' });
  contenido = el('div', { class: 'contenido' });
  tabs = el('nav', { class: 'tabs' });
  raiz.append(el('header', { class: 'cabecera' }, el('h1', { class: 'marca-movil' }, logo('logo-cab'), 'AquaPlants'),
    el('span', { class: 'cab-der' }, el('span', { class: 'nota' }, cabPunto, RELE ? 'vía Firebase' : 'en vivo'), campana((n) => {
      cerrarTodo(); clearInterval(est.timerTab);
      if (!esTecnico() && destinoAviso(n) === 'servicio') { abrirPantalla('Servicio técnico', (c) => vServicioTecnico(c)); return; }
      est.tab = esTecnico() ? 'solicitudes' : 'alertas'; est.montada = null; pintarTabs(); pintar();
    }))), contenido, tabs);
  est.tab = esTecnico() ? 'solicitudes' : 'hoy'; est.montada = null; pintarTabs(); refrescar();
  clearInterval(temporizador); temporizador = setInterval(refrescar, RELE ? 6000 : 5000);
}
function pintarTabs() {
  const n = datos ? datos.panel.totales.alertas_abiertas : 0;
  const pestanas = esTecnico() ? [['solicitudes', '🔧', 'Solicitudes'], ['ubicacion', '📍', 'Equipos'], ['estadisticas', '📈', 'Resumen']]
    : [['hoy', '🏠', 'Hoy'], ['cultivos', '🌿', 'Cultivos'], ['alertas', '🔔', 'Alertas'], ['mas', '🧰', esAdmin() ? 'Gestión' : 'Más']];
  pestanas.push(['cuenta', '👤', 'Cuenta']);
  tabs.className = `tabs n${pestanas.length}`;
  vaciar(tabs).append(...pestanas.map(([id, ico, t]) =>
    el('button', { class: est.tab === id ? 'activo' : '', type: 'button', onclick: () => { cerrarTodo(); clearInterval(est.timerTab); est.tab = id; est.montada = null; AQ.editando = false; pintarTabs(); pintar(); } },
      el('span', { class: 'ico' }, ico), t, id === 'alertas' && n ? el('span', { class: 'cuenta' }, n) : null)));
}

/* pantallas apiladas (botón «Volver» y botón atrás de Android)  */
function abrirPantalla(titulo, construir) {
  const cuerpo = el('div', { class: 'cuerpo' }, el('p', { class: 'nota' }, 'Cargando…'));
  const h = { cuerpo, recargar: null };
  h.nodo = el('div', { class: 'hoja' }, el('header', { class: 'cabecera' }, el('button', { class: 'atras', type: 'button', onclick: cerrarPantalla }, '‹ Volver'),
    el('span', { class: 'hoja-titulo' }, titulo)), cuerpo);
  pila.push(h); document.body.append(h.nodo);
  h.recargar = async () => { try { await construir(cuerpo, h); } catch (e) { vaciar(cuerpo).append(el('p', { class: 'aviso error' }, mensajeError(e))); } };
  h.recargar();
  return h;
}
function cerrarPantalla() {
  const h = pila.pop();
  if (h) h.nodo.remove();
  if (!pila.length) { hojaVivos = []; pintar(); }
}
function cerrarTodo() { while (pila.length) pila.pop().nodo.remove(); hojaVivos = []; }
/* Lo llama la app Android al presionar «atrás». Devuelve true si la app lo manejó (si no, Android cierra la app). */
window.aqAtras = () => {
  const panel = document.querySelector('.panel-avisos:not(.oculto)');
  if (panel) { panel.classList.add('oculto'); return true; }
  if (pila.length) { cerrarPantalla(); return true; }
  if (AQ.usuario && est.tab !== 'hoy') { est.tab = 'hoy'; pintarTabs(); pintar(); return true; }
  return false;
};

/*  datos  */
async function refrescar() {
  if (esTecnico()) {                                    // el técnico no ve cultivos ni alertas: solo sus solicitudes, los equipos y su cuenta
    datos = datos || { panel: { totales: { alertas_abiertas: 0 } }, estados: [], abiertas: [] };
    cabPunto.className = 'punto'; if (!est.montada) { pintarTabs(); pintar(); } return;
  }
  try {
    const r = await api.get('/movil/resumen');
    datos = r;
    actualizarVivos();
    cabPunto.className = 'punto';
    const ids = new Set(r.abiertas.map((a) => a.id));
    if (conocidas) {                                      // avisa (y vibra) cuando entra una alerta nueva
      const nuevas = r.abiertas.filter((a) => !conocidas.has(a.id));
      if (nuevas.length) { mostrarAviso(`${tituloAlerta(nuevas[0])} · ${nuevas[0].cultivo || nuevas[0].instalacion}`, 'error'); if (navigator.vibrate) navigator.vibrate([200, 100, 200]); }
    }
    conocidas = ids;
    pintarTabs(); pintar();
  } catch (e) {
    cabPunto.className = 'punto mal';
    if (!datos) vaciar(contenido).append(el('p', { class: 'aviso error' }, mensajeError(e)), el('button', { class: 'boton sec', type: 'button', onclick: refrescar }, 'Reintentar'));
  }
}
function ocupado() {
  const a = document.activeElement;
  return a && ['TEXTAREA', 'SELECT', 'INPUT'].includes(a.tagName);
}
function pintar() {
  if (!datos || pila.length || ocupado() || AQ.editando) return;           // no se redibuja mientras se escribe o hay una pantalla encima
  ({ hoy: vHoy, cultivos: vCultivos, alertas: vAlertas, mas: vMas, cuenta: vCuenta, solicitudes: () => montarTab(vServicioTecnico), ubicacion: () => montarTab(vUbicacion),
    estadisticas: () => montarTab(vEstadisticas) })[est.tab]();
}
/* Pestañas del servicio técnico, ubicación y estadísticas: se dibujan una vez (no cada 5 segundos) y se refrescan solas si les corresponde. */
async function montarTab(construir) {
  if (est.montada === est.tab) return;
  est.montada = est.tab; clearInterval(est.timerTab);
  const zona = el('div', { class: 'zona-tab' }); vaciar(contenido).append(zona);
  try { const r = await construir(zona); if (r && r.refrescar && est.montada === est.tab) est.timerTab = setInterval(() => { r.refrescar().catch(() => {}); }, r.cada || 15000); }
  catch (e) { vaciar(zona).append(el('p', { class: 'aviso error' }, mensajeError(e))); est.montada = null; }
}
function actualizarVivos() {
  for (const v of hojaVivos) {
    const l = datos.estados.flat().find((e) => e.sensor_id === v.sensor_id);
    if (l) { v.tVal.nodeValue = fmt(l.valor, 2); v.tHace.nodeValue = hace(l.medido_en); }
  }
}
function cultivosDe(i, k) {                                  // agrupa las lecturas por cultivo (= por dispositivo)
  return i.cultivos.map((c) => ({ ...c, instalacion: i.nombre, instalacion_id: i.id, lecturas: (datos.estados[k] || []).filter((e) => e.cultivo_id === c.id) }));
}
const todosLosCultivos = () => datos.panel.instalaciones.flatMap((i, k) => cultivosDe(i, k));

/*  piezas  */
function nivelCultivo(c) {
  const f = c.fallas || [];
  if (c.estado_conexion === 'sin_comunicacion' || f.some((x) => x.estado === 'activa')) return 'critica';
  return f.length ? 'advertencia' : 'ok';
}
function chipCultivo(c) {
  const f = c.fallas || [];
  const t = c.estado_conexion === 'sin_comunicacion' ? 'Sin comunicación'
    : f.some((x) => x.estado === 'activa') ? 'Con falla' : f.some((x) => x.estado === 'reconocida') ? 'En revisión'
      : f.length ? 'Falta confirmar' : c.estado_conexion === 'en_linea' ? 'Operando normal' : 'Sin datos';
  return el('span', { class: `chip ${nivelCultivo(c)}` }, t);
}
/* Estado del dispositivo: qué falla tiene, quién la atiende, o la última solución confirmada. */
function estadoDispositivo(c) {
  const f = c.fallas || [], u = c.ultima_solucion;
  if (!f.length && !u) return null;
  return el('div', { class: 'fallas' },
    f.map((x) => el('div', { class: `falla ${x.estado}` },
      el('strong', {}, tituloAlerta(x)), ` · ${ETIQUETA_FALLA[x.estado] || x.estado}${x.por ? ` (${x.por})` : ''}${x.no_solucionado ? ' · aún sin solución' : ''}`)),
    u ? el('div', { class: 'falla resuelta' }, el('strong', {}, `✔ Solucionado: ${u.problema || 'falla'}`),
      el('div', { class: 'nota' }, [u.auto ? 'se resolvió sola (valor normal)' : '', u.solucion_por ? `solución de ${u.solucion_por}` : '', u.por ? `confirmó ${u.por}` : '', hace(u.en)].filter(Boolean).join(' · '))) : null);
}
function tarjetaCultivo(c) {
  const conAlerta = c.lecturas.filter((l) => l.alertas_abiertas);
  const clave = c.lecturas.filter((l) => !l.alertas_abiertas && LECTURAS_CLAVE.includes(l.tipo_variable))
    .sort((a, b) => LECTURAS_CLAVE.indexOf(a.tipo_variable) - LECTURAS_CLAVE.indexOf(b.tipo_variable));
  return el('div', { class: `tarjeta cultivo-t ${nivelCultivo(c)}`, onclick: () => abrirCultivo(c) },
    el('div', { class: 'fila' }, el('div', {}, el('h3', {}, c.nombre), el('div', { class: 'nota' }, `Dispositivo ${c.dispositivo_codigo} · ${c.instalacion}`)), chipCultivo(c)),
    estadoDispositivo(c),
    el('div', { class: 'mini' }, [...conAlerta, ...clave].slice(0, 4).map((l) => el('div', { class: `dato ${l.alertas_abiertas ? 'critica' : 'ok'}` },
      el('div', { class: 'n' }, l.tipo_nombre), el('div', { class: 'v' }, fmt(l.valor, 2), ' ', el('span', { class: 'u' }, l.unidad))))),
    el('div', { class: 'nota ver-mas' }, 'Ver detalle ›'));
}
function tarjetaAlerta(a, alTerminar) {
  return el('div', { class: `tarjeta alerta ${a.tipo === 'sin_comunicacion' ? 'critica' : ''} ${a.estado}` },
    el('div', { class: 'fila' }, el('h3', {}, tituloAlerta(a)), el('span', { class: `chip ${a.estado}` }, ETIQUETA[a.estado] || a.estado)),
    el('div', { class: 'meta' }, `${a.cultivo || a.instalacion} · ${[a.dispositivo_codigo, a.sensor_codigo].filter(Boolean).join(' · ')} · ${fechaHora(a.creada_en)}`),
    el('div', { class: 'msg' }, a.mensaje),
    a.solucion_cuestionada && a.estado !== 'resuelta' ? el('span', { class: 'chip cuestionada' }, 'Solución cuestionada') : null,
    a.mantencion ? el('span', { class: `chip m-${a.mantencion.estado}` }, `Mantención: ${a.mantencion.estado_texto.toLowerCase()}`) : null,
    ...trazaAlerta(a),
    accionesAlerta(a, async () => { await refrescar(); if (alTerminar) await alTerminar(); }, 'boton'));
}
const campo = (etiqueta, entrada, ayuda) => el('label', { class: 'campo-m' }, el('span', {}, etiqueta), entrada, ayuda ? el('small', { class: 'nota' }, ayuda) : null);
const seleccion = (pares, valor) => el('select', {}, pares.map(([v, t]) => el('option', { value: v, selected: String(v) === String(valor ?? '') }, t)));
const interruptor = (activo, alCambiar) => el('label', { class: 'interruptor' }, el('input', { type: 'checkbox', checked: activo, onchange: (e) => alCambiar(e.target.checked, e.target) }), el('span', {}, activo ? 'Activo' : 'Inactivo'));
async function accion(fn, ok, despues) {
  try { await fn(); if (ok) mostrarAviso(ok, 'ok'); if (despues) await despues(); } catch (e) { mostrarAviso(mensajeError(e), 'error'); }
}

/*  Hoy  */
function vHoy() {
  const t = datos.panel.totales, n = t.alertas_abiertas, todos = todosLosCultivos();
  const nivel = n || t.sin_comunicacion ? 'critica' : 'ok';
  vaciar(contenido).append(
    el('div', { class: `banner ${nivel}` }, el('div', { class: 'grande' }, n ? `${n} ${n === 1 ? 'falla requiere' : 'fallas requieren'} atención` : 'Todo en orden'),
      el('div', { class: 'peq' }, `${t.en_linea} de ${t.dispositivos} dispositivos en línea · ${todos.length} cultivos`)),
    ...(n ? [el('h2', {}, 'Para atender ahora'), ...datos.abiertas.slice(0, 8).map((a) => tarjetaAlerta(a))] : []),
    el('h2', {}, 'Tus cultivos'), ...(todos.length ? todos.map(tarjetaCultivo) : [el('p', { class: 'nota' }, 'No tienes cultivos asignados.')]));
}

/*  Cultivos y detalle  */
function vCultivos() {
  vaciar(contenido).append(...datos.panel.instalaciones.flatMap((i, k) => [el('h2', {}, i.nombre), ...cultivosDe(i, k).map(tarjetaCultivo)]));
}
function abrirCultivo(c0) {
  abrirPantalla(c0.nombre, async (cuerpo) => {
    const c = todosLosCultivos().find((x) => x.id === c0.id) || c0;
    const [plantas, reglas, abiertas] = await Promise.all([api.get(`/plantas${qs({ cultivo_id: c.id })}`), api.get('/reglas-alerta'),
      api.get(`/alertas${qs({ estado: 'abiertas', instalacion_id: c.instalacion_id, limite: 50 })}`)]);
    const mias = abiertas.filter((a) => a.dispositivo_codigo === c.dispositivo_codigo);
    hojaVivos = [];
    vaciar(cuerpo).append(
      el('div', { class: 'fila' }, el('div', {}, el('h2', {}, c.nombre), el('p', { class: 'nota' }, `Dispositivo ${c.dispositivo_codigo} · ${c.instalacion}`)), chipCultivo(c)),
      el('p', { class: `aviso ${c.estado_conexion === 'en_linea' ? 'ok' : 'error'}` }, `${ETIQUETA[c.estado_conexion] || c.estado_conexion}${c.lecturas[0] ? ` · última comunicación ${hace(c.lecturas[0].ultima_comunicacion)}` : ''}`),
      estadoDispositivo(c),
      mias.length ? [el('h2', {}, 'Fallas abiertas'), ...mias.map((a) => tarjetaAlerta(a, () => cerrarYReabrir(c)))] : null,
      esAdmin() ? el('button', { class: 'boton sec', type: 'button', onclick: () => abrirSimulacion(c.dispositivo_codigo) }, '🧪 Simular una falla en este cultivo') : null,
      el('p', { class: 'nota' }, `Plantas: ${plantas.map((p) => p.nombre).join(', ') || 'sin plantas registradas'}`),
      el('h2', {}, 'Sensores (últimas 2 horas)'));
    const hasta = new Date(), desde = new Date(hasta.getTime() - 2 * 3600 * 1000);
    const orden = (l) => (l.alertas_abiertas ? -1 : LECTURAS_CLAVE.includes(l.tipo_variable) ? LECTURAS_CLAVE.indexOf(l.tipo_variable) : 99);
    for (const l of [...c.lecturas].sort((a, b) => orden(a) - orden(b))) {
      const g = el('div', {}), tVal = document.createTextNode(fmt(l.valor, 2)), tHace = document.createTextNode(hace(l.medido_en));
      hojaVivos.push({ sensor_id: l.sensor_id, tVal, tHace });
      const refs = reglas.filter((r) => r.activa && r.sensor_id === l.sensor_id).map((r) => ({ nombre: `${r.operador} ${r.umbral}`, valor: r.umbral }));
      cuerpo.append(el('div', { class: `tarjeta ${l.alertas_abiertas ? 'con-alerta' : ''}` },
        el('div', { class: 'fila' }, el('h3', {}, l.tipo_nombre), el('span', { class: 'nota' }, tHace)),
        el('div', { class: 'dato' }, el('div', { class: 'v' }, tVal, ' ', el('span', { class: 'u' }, l.unidad)),
          refs.length ? el('div', { class: 'n' }, `Normal: ${refs.map((r) => r.nombre).join(' y ')} genera alerta`) : null,
          l.alertas_abiertas ? el('div', { class: 'n alerta-n' }, `${l.alertas_abiertas} falla(s) abierta(s)`) : null), g));
      api.get(`/mediciones${qs({ sensor_id: l.sensor_id, desde: desde.toISOString(), hasta: hasta.toISOString() })}`)
        .then((m) => vaciar(g).append(grafico(puntosDe(m), { unidad: l.unidad, alto: 90, compacto: true, referencias: refs }))).catch(() => {});
    }
  });
}
function cerrarYReabrir(c) { const h = pila[pila.length - 1]; if (h) h.recargar(); }

/*  Alertas  */
const FILTROS_ALERTA = [['abiertas', 'Abiertas'], ['revisada', 'Falta confirmar'], ['no_solucionadas', 'Sin solución'], ['resuelta', 'Resueltas']];
async function vAlertas() {
  const filtro = est.filtro;
  const lista = filtro === 'abiertas' ? datos.abiertas : await api.get(`/alertas${qs({ estado: filtro, limite: 60 })}`);
  if (est.tab !== 'alertas' || filtro !== est.filtro || pila.length || ocupado()) return;
  vaciar(contenido).append(
    el('div', { class: 'segmento' }, FILTROS_ALERTA.map(([f, t]) => el('button', { type: 'button', class: est.filtro === f ? 'activo' : '', onclick: () => { est.filtro = f; vAlertas(); } }, t))),
    ...(lista.length ? lista.map((a) => tarjetaAlerta(a, () => vAlertas())) : [el('p', { class: 'aviso ok' }, 'No hay alertas con este filtro.')]));
}

/*  Más / Gestión (supervisor y administrador): todo dentro de la app  */
function vMas() {
  const items = [
    ['📒', 'Bitácora de alertas', 'Quién reconoció, dio solución, no pudo solucionar, confirmó o rechazó cada falla.', hBitacora, esSupervision()],
    ['📊', 'Comparar cultivos', 'Promedio, mínimo y máximo de una variable en cada cultivo.', hComparacion, esSupervision()],
    ['📡', 'Dispositivos', 'Conexión, última comunicación y calidad de enlace de cada AquaBrain.', hDispositivos, esSupervision()],
    ['🔧', 'Servicio técnico', 'Pedidos de arreglo, reemplazo, retiro o mantención y su estado.', (c) => vServicioTecnico(c), true],
    ['📍', 'Ubicación de equipos', 'Dónde está cada dispositivo según su GPS, su recorrido y cómo llegar.', (c) => vUbicacion(c), true],
    ['📈', 'Estadísticas', 'Alertas, tiempos de respuesta, cortes, dispositivos y mantenciones.', (c) => vEstadisticas(c), true],
    ['🧪', 'Simular fallas', 'Falta o exceso de agua, nutrientes, luz, gases, pH, temperatura o corte de comunicación.', (c, h) => hSimulacion(c, h), esAdmin()],
    ['👥', 'Usuarios', 'Crear usuarios, cambiar rol, instalaciones asignadas, activar o desactivar.', hUsuarios, esAdmin()],
    ['🎚️', 'Reglas de alerta', 'Umbrales de cada cultivo: cambiar el valor o desactivar una regla.', hReglas, esAdmin()],
    ['🏭', 'Instalaciones y cultivos', 'Crear y activar o desactivar instalaciones y cultivos.', hInstalaciones, esAdmin()],
    ['⚙️', 'Parámetros', 'Minutos sin comunicación y retención de históricos.', hParametros, esAdmin()],
    ['📱', 'Equipos vinculados', 'Celulares y navegadores de cada usuario; revocar o reactivar.', hEquipos, esAdmin()],
    ['🛡️', 'Auditoría', 'Registro de todas las acciones del sistema.', hAuditoria, esAdmin()],
    ['📥', 'Informes', 'Descargar Excel o CSV de mediciones, alertas y eventos.', hInformes, esAdmin()],
  ].filter((x) => x[4]);
  vaciar(contenido).append(el('h2', {}, esAdmin() ? 'Gestión del sistema' : esSupervision() ? 'Herramientas de supervisión' : 'Más'),
    ...items.map(([ico, t, d, fn]) => el('button', { class: 'tarjeta item-mas', type: 'button', onclick: () => abrirPantalla(t, fn) },
      el('span', { class: 'item-ico' }, ico), el('span', { class: 'item-txt' }, el('strong', {}, t), el('span', { class: 'nota' }, d)), el('span', { class: 'item-flecha' }, '›'))));
}

async function hBitacora(cuerpo) {
  const lista = await api.get('/alertas?limite=200');
  const filtro = { v: 'todas' };
  const zona = el('div', {});
  const pintarB = () => {
    const ev = lista.flatMap((a) => (a.bitacora || []).map((b) => ({ ...b, a })))
      .filter((b) => filtro.v === 'todas' || (filtro.v === 'soluciones' && ['dio solución', 'resolvió', 'confirmó la solución'].includes(b.accion))
        || (filtro.v === 'sin_solucion' && ['no pudo solucionar', 'rechazó la solución'].includes(b.accion)))
      .sort((x, y) => String(y.en).localeCompare(String(x.en)));
    vaciar(zona).append(ev.length ? ev.slice(0, 150).map((b) => el('div', { class: `tarjeta bitacora ${/no pudo|rechaz/.test(b.accion) ? 'mal' : /soluci|resolvi/.test(b.accion) ? 'bien' : ''}` },
      el('div', { class: 'fila' }, el('strong', {}, b.por), el('span', { class: 'nota' }, fechaHora(b.en))),
      el('div', {}, `${ROL[b.rol] || b.rol} · ${b.accion}`),
      el('div', { class: 'nota' }, `${tituloAlerta(b.a)} · ${b.a.cultivo || b.a.instalacion} · ${b.a.dispositivo_codigo || ''} · estado actual: ${ETIQUETA[b.a.estado] || b.a.estado}`),
      b.nota ? el('div', { class: 'cita' }, `«${b.nota}»`) : null)) : el('p', { class: 'aviso' }, 'Todavía no hay acciones registradas.'));
  };
  vaciar(cuerpo).append(el('div', { class: 'segmento' }, [['todas', 'Todo'], ['soluciones', 'Soluciones'], ['sin_solucion', 'Sin solución']].map(([v, t]) =>
    el('button', { type: 'button', class: filtro.v === v ? 'activo' : '', onclick: (e) => { filtro.v = v; e.target.parentNode.querySelectorAll('button').forEach((b) => b.classList.toggle('activo', b === e.target)); pintarB(); } }, t))), zona);
  pintarB();
}

async function hComparacion(cuerpo) {
  const tipos = await api.get('/tipos-sensor');
  const variable = seleccion(tipos.map((t) => [t.tipo_variable, `${t.nombre} (${t.unidad})`]), 'nivel_agua');
  const periodo = seleccion([['6', 'Últimas 6 horas'], ['24', 'Últimas 24 horas'], ['168', 'Últimos 7 días']], '6');
  const zona = el('div', {});
  const cargar = async () => {
    vaciar(zona).append(el('p', { class: 'nota' }, 'Cargando…'));
    const hasta = new Date(), desde = new Date(hasta.getTime() - Number(periodo.value) * 3600 * 1000);
    try {
      const d = await api.get(`/comparacion${qs({ tipo_variable: variable.value, desde: desde.toISOString(), hasta: hasta.toISOString() })}`);
      vaciar(zona).append(d.instalaciones.length ? d.instalaciones.map((x) => el('div', { class: 'tarjeta' }, el('h3', {}, x.nombre),
        el('div', { class: 'mini tres' }, [['Promedio', x.promedio], ['Mínimo', x.minimo], ['Máximo', x.maximo]].map(([n, v]) =>
          el('div', { class: 'dato' }, el('div', { class: 'n' }, n), el('div', { class: 'v' }, fmt(v, 2), ' ', el('span', { class: 'u' }, x.unidad))))),
        grafico((x.serie || []).map((p) => ({ t: Date.parse(p.t), v: p.prom })), { unidad: x.unidad, alto: 90, compacto: true })))
        : el('p', { class: 'aviso' }, 'Sin datos para esa variable.'));
    } catch (e) { vaciar(zona).append(el('p', { class: 'aviso error' }, mensajeError(e))); }
  };
  variable.addEventListener('change', cargar); periodo.addEventListener('change', cargar);
  vaciar(cuerpo).append(campo('Variable', variable), campo('Período', periodo), zona);
  cargar();
}

async function hDispositivos(cuerpo, h) {
  const lista = await api.get('/dispositivos');
  vaciar(cuerpo).append(...lista.map((d) => el('div', { class: 'tarjeta' },
    el('div', { class: 'fila' }, el('div', {}, el('h3', {}, `${d.codigo} · ${d.cultivo_nombre || 'sin cultivo'}`), el('div', { class: 'nota' }, `${d.nombre} · ${d.instalacion_codigo}`)),
      el('span', { class: `chip ${d.estado_conexion === 'en_linea' ? 'ok' : 'critica'}` }, ETIQUETA[d.estado_conexion] || d.estado_conexion)),
    el('div', { class: 'nota' }, `Última comunicación: ${hace(d.ultima_comunicacion)}${d.calidad_enlace != null ? ` · enlace ${d.calidad_enlace}%` : ''}`),
    esAdmin() ? interruptor(d.activo, (v) => accion(() => api.patch(`/dispositivos/${d.id}`, { activo: v }), v ? 'Dispositivo activado' : 'Dispositivo desactivado', h.recargar)) : null)));
}

async function abrirSimulacion(codigo) { abrirPantalla('Simular fallas', (c, h) => hSimulacion(c, h, codigo)); }
async function hSimulacion(cuerpo, h, preseleccion) {
  const s = await api.get('/simulacion');
  if (!s.disponible) { vaciar(cuerpo).append(el('p', { class: 'aviso' }, 'La simulación no está activa en este servidor (AQ_SIMULACION=0 o dispositivos reales).')); return; }
  const disp = seleccion(s.dispositivos.map((d) => [d.codigo, `${d.cultivo} · ${d.codigo}`]), preseleccion || (h.ultimo || s.dispositivos[0].codigo));
  disp.addEventListener('change', () => { h.ultimo = disp.value; });
  const ejecutar = (cuerpoAccion, ok) => accion(() => api.post('/simulacion/accion', { ...cuerpoAccion, dispositivo: disp.value }), ok, async () => { h.ultimo = disp.value; await h.recargar(); });
  const etiqueta = Object.fromEntries(s.escenarios.map((e) => [e.codigo, e.etiqueta]));
  vaciar(cuerpo).append(
    el('p', { class: 'nota' }, 'Elige el cultivo y la falla. La alerta aparece en segundos (cuando el dispositivo envía su lectura) y la falla sigue hasta que alguien da la solución. «Sin comunicación» aparece después de los minutos configurados en Parámetros.'),
    campo('Cultivo / dispositivo', disp),
    el('div', { class: 'rejilla-botones' }, s.escenarios.map((e) => el('button', { type: 'button', class: `boton sec ${e.codigo === 'sin_comunicacion' ? 'peligro' : ''}`,
      onclick: () => ejecutar({ accion: 'escenario', escenario: e.codigo }, `Falla provocada: ${e.etiqueta}`) }, e.etiqueta))),
    el('button', { type: 'button', class: 'boton', onclick: () => ejecutar({ accion: 'reparar' }, 'Falla terminada en el dispositivo simulado') }, '🔧 Terminar la falla de este dispositivo'),
    el('h2', {}, 'Estado de los dispositivos simulados'),
    ...s.dispositivos.map((d) => el('div', { class: 'tarjeta' }, el('div', { class: 'fila' }, el('strong', {}, `${d.cultivo} · ${d.codigo}`),
      el('span', { class: `chip ${d.escenario ? 'critica' : 'ok'}` }, d.escenario ? (etiqueta[d.escenario] || d.escenario) : 'Normal')),
      el('div', { class: 'nota' }, `En cola local: ${d.en_cola} · enviadas ${d.aceptadas} · rechazadas ${d.rechazadas}`))),
    el('h2', {}, 'Simulación'),
    el('button', { type: 'button', class: 'boton sec', onclick: () => accion(() => api.post('/simulacion/accion', { accion: s.activa ? 'pausar' : 'reanudar' }), s.activa ? 'Simulación pausada' : 'Simulación reanudada', h.recargar) },
      s.activa ? '⏸ Pausar todos los dispositivos' : '▶ Reanudar los dispositivos'),
    el('label', { class: 'campo-check' }, el('input', { type: 'checkbox', checked: s.opciones.anomalias, onchange: () => accion(() => api.post('/simulacion/accion', { accion: 'alternar_anomalias' }), 'Listo', h.recargar) }), ' Fallas al azar de vez en cuando'),
    el('label', { class: 'campo-check' }, el('input', { type: 'checkbox', checked: s.opciones.datos_erroneos, onchange: () => accion(() => api.post('/simulacion/accion', { accion: 'alternar_datos_erroneos' }), 'Listo', h.recargar) }), ' Datos erróneos ocasionales (la API los rechaza)'));
}

async function hUsuarios(cuerpo, h) {
  const [usuarios, inst, disp] = await Promise.all([api.get('/usuarios'), api.get('/instalaciones'), api.get('/dispositivos')]);
  h.disp = disp;
  const nomInst = Object.fromEntries(inst.map((i) => [i.id, i.nombre]));
  vaciar(cuerpo).append(el('button', { class: 'boton', type: 'button', onclick: () => abrirPantalla('Nuevo usuario', (c, h2) => formUsuario(c, h2, null, inst, h)) }, '+ Nuevo usuario'),
    ...usuarios.map((u) => el('button', { type: 'button', class: 'tarjeta item-mas', onclick: () => abrirPantalla(u.nombre, (c, h2) => formUsuario(c, h2, u, inst, h)) },
      el('span', { class: 'item-txt' }, el('strong', {}, u.nombre), el('span', { class: 'nota' }, `${u.email} · ${ROL[u.rol]}${u.rol === 'encargado_cultivo' ? ` · ${u.instalaciones.map((i) => nomInst[i]).join(', ') || 'sin instalaciones'}` : ''}`)),
      el('span', { class: `chip ${u.activo ? 'ok' : 'critica'}` }, u.activo ? 'Activo' : 'Inactivo'))));
}
function formUsuario(cuerpo, h, u, inst, padre) {
  const nombre = el('input', { value: u ? u.nombre : '', autocomplete: 'off' }), email = el('input', { type: 'email', value: u ? u.email : '', disabled: !!u, autocomplete: 'off' });
  const rol = seleccion(Object.entries(ROL), u ? u.rol : 'encargado_cultivo'), clave = el('input', { type: 'password', autocomplete: 'new-password', placeholder: u ? 'Dejar vacío para no cambiarla' : '12+ caracteres, mayúscula, número y símbolo' });
  const marcadas = new Set(u ? u.instalaciones : []), aCargo = new Set(u ? u.dispositivos : []), disp = padre.disp || [];
  const activo = el('input', { type: 'checkbox', checked: u ? u.activo : true }), motivo = el('input', { value: u && u.suspension ? u.suspension.motivo || '' : '', placeholder: 'Motivo si suspendes la cuenta' });
  const guardar = async () => {
    const cuerpoU = { nombre: nombre.value.trim(), rol: rol.value, instalaciones: [...marcadas], dispositivos: [...aCargo] };
    if (u && !activo.checked) cuerpoU.suspension_motivo = motivo.value.trim();
    if (clave.value) cuerpoU.password = clave.value;
    if (u) cuerpoU.activo = activo.checked;
    await accion(async () => {
      if (u) await api.patch(`/usuarios/${u.id}`, cuerpoU);
      else await api.post('/usuarios', { ...cuerpoU, email: email.value.trim(), password: clave.value });
      cerrarPantalla(); padre.recargar();
    }, u ? 'Usuario actualizado' : 'Usuario creado');
  };
  vaciar(cuerpo).append(campo('Nombre', nombre), campo('Correo', email), campo('Rol', rol),
    el('fieldset', { class: 'grupo' }, el('legend', {}, 'Instalaciones (encargado de cultivo)'), inst.map((i) => el('label', { class: 'campo-check' },
      el('input', { type: 'checkbox', checked: marcadas.has(i.id), onchange: (e) => { if (e.target.checked) marcadas.add(i.id); else marcadas.delete(i.id); } }), ' ', i.nombre))),
    el('fieldset', { class: 'grupo' }, el('legend', {}, 'Dispositivos a cargo (reciben sus alertas)'), disp.map((d) => el('label', { class: 'campo-check' },
      el('input', { type: 'checkbox', checked: aCargo.has(d.id), onchange: (e) => { if (e.target.checked) aCargo.add(d.id); else aCargo.delete(d.id); } }), ` ${d.cultivo_nombre || 'Sin cultivo'} · ${d.codigo}`))),
    campo(u ? 'Nueva contraseña (opcional)' : 'Contraseña', clave), u ? el('label', { class: 'campo-check' }, activo, ' Cuenta activa (desmárcala para suspender)') : null, u ? campo('Motivo de la suspensión', motivo) : null,
    el('button', { class: 'boton', type: 'button', onclick: guardar }, u ? 'Guardar cambios' : 'Crear usuario'),
    u ? el('button', { class: 'boton sec', type: 'button', onclick: async () => {
      if (!window.confirm(`¿Eliminar la cuenta de ${u.nombre}? No podrá volver a entrar; su nombre se conserva en el historial.`)) return;
      await accion(async () => { await api.del(`/usuarios/${u.id}`); cerrarPantalla(); padre.recargar(); }, 'Cuenta eliminada'); } }, 'Eliminar cuenta') : null);
}

async function hReglas(cuerpo, h) {
  const [reglas, sensores] = await Promise.all([api.get('/reglas-alerta'), api.get('/sensores')]);
  const sens = Object.fromEntries(sensores.map((s) => [s.id, s]));
  const grupos = {};
  for (const r of reglas) { const k = (sens[r.sensor_id] && sens[r.sensor_id].cultivo_nombre) || 'Generales (por tipo de sensor)'; (grupos[k] = grupos[k] || []).push(r); }
  const filtro = seleccion(Object.keys(grupos).sort().map((k) => [k, k]), h.cultivo || Object.keys(grupos).sort()[0]);
  const zona = el('div', {});
  const pintarR = () => {
    h.cultivo = filtro.value;
    vaciar(zona).append((grupos[filtro.value] || []).map((r) => {
      const umbral = el('input', { type: 'number', step: 'any', value: r.umbral, class: 'corto' });
      const s = sens[r.sensor_id];
      return el('div', { class: `tarjeta ${r.activa ? '' : 'apagada'}` }, el('div', { class: 'fila' }, el('strong', {}, `${r.severidad === 'emergencia' ? '🚨 ' : ''}${r.nombre.split(':').slice(-1)[0].trim()}`),
        interruptor(r.activa, (v) => accion(() => api.patch(`/reglas-alerta/${r.id}`, { activa: v }), v ? 'Regla activada' : 'Regla desactivada', h.recargar))),
      el('div', { class: 'nota' }, `${s ? `${s.tipo_nombre} (${s.codigo})` : r.tipo_nombre || ''} · alerta si el valor es ${r.operador} ${r.umbral}${s ? ' ' + s.unidad : ''}`),
      el('div', { class: 'fila' }, el('span', {}, `Umbral (${r.operador})`), umbral,
        el('button', { class: 'boton chico', type: 'button', onclick: () => accion(() => api.patch(`/reglas-alerta/${r.id}`, { umbral: Number(umbral.value) }), 'Umbral actualizado', h.recargar) }, 'Guardar')));
    }));
  };
  filtro.addEventListener('change', pintarR);
  vaciar(cuerpo).append(campo('Cultivo', filtro), zona); pintarR();
}

async function hInstalaciones(cuerpo, h) {
  const [inst, cult] = await Promise.all([api.get('/instalaciones'), api.get('/cultivos')]);
  const nuevaInst = () => abrirPantalla('Nueva instalación', (c) => {
    const codigo = el('input', { placeholder: 'INV-ESTE' }), nombre = el('input', {}), desc = el('input', {}), ubic = el('input', { placeholder: 'Dirección o sector' });
    vaciar(c).append(campo('Código', codigo, 'Letras, números y guiones'), campo('Nombre', nombre), campo('Ubicación', ubic), campo('Descripción', desc),
      el('button', { class: 'boton', type: 'button', onclick: () => accion(async () => { await api.post('/instalaciones', { codigo: codigo.value.trim(), nombre: nombre.value.trim(), ubicacion: ubic.value.trim(), descripcion: desc.value.trim() }); cerrarPantalla(); h.recargar(); }, 'Instalación creada') }, 'Crear'));
  });
  const nuevoCultivo = (iid) => abrirPantalla('Nuevo cultivo', (c) => {
    const nombre = el('input', {}), desc = el('input', {});
    vaciar(c).append(el('p', { class: 'nota' }, `Instalación: ${inst.find((i) => i.id === iid).nombre}. Luego asígnale su dispositivo desde la web (consola).`), campo('Nombre', nombre), campo('Descripción', desc),
      el('button', { class: 'boton', type: 'button', onclick: () => accion(async () => { await api.post('/cultivos', { instalacion_id: iid, nombre: nombre.value.trim(), descripcion: desc.value.trim() }); cerrarPantalla(); h.recargar(); }, 'Cultivo creado') }, 'Crear'));
  });
  vaciar(cuerpo).append(el('button', { class: 'boton', type: 'button', onclick: nuevaInst }, '+ Nueva instalación'),
    ...inst.map((i) => el('div', { class: 'tarjeta' },
      el('div', { class: 'fila' }, el('div', {}, el('h3', {}, i.nombre), el('div', { class: 'nota' }, `${i.codigo}${i.ubicacion ? ' · 📍 ' + i.ubicacion : ''}${i.descripcion ? ' · ' + i.descripcion : ''}`)),
        interruptor(i.activa, (v) => accion(() => api.patch(`/instalaciones/${i.id}`, { activa: v }), 'Instalación actualizada', h.recargar))),
      ...cult.filter((c) => c.instalacion_id === i.id).map((c) => el('div', { class: 'fila sub' }, el('span', {}, `🌿 ${c.nombre}`),
        interruptor(c.activo, (v) => accion(() => api.patch(`/cultivos/${c.id}`, { activo: v }), 'Cultivo actualizado', h.recargar)))),
      el('button', { class: 'boton chico sec', type: 'button', onclick: () => nuevoCultivo(i.id) }, '+ Cultivo'))));
}

async function hParametros(cuerpo, h) {
  const ps = await api.get('/parametros');
  vaciar(cuerpo).append(...ps.map((p) => {
    const v = el('input', { type: 'number', min: 0, value: p.valor, class: 'corto' });
    return el('div', { class: 'tarjeta' }, el('strong', {}, p.clave.replace(/_/g, ' ')), el('div', { class: 'nota' }, p.descripcion),
      el('div', { class: 'fila' }, v, el('button', { class: 'boton chico', type: 'button', onclick: () => accion(() => api.put(`/parametros/${p.clave}`, { valor: String(v.value) }), 'Parámetro guardado', h.recargar) }, 'Guardar')));
  }));
}

async function hEquipos(cuerpo, h) {
  const lista = await api.get('/equipos');
  vaciar(cuerpo).append(lista.length ? lista.map((x) => el('div', { class: 'tarjeta' },
    el('div', { class: 'fila' }, el('div', {}, el('strong', {}, x.nombre), el('div', { class: 'nota' }, `${x.usuario || '—'} (${ROL[x.rol] || x.rol || ''}) · ${x.plataforma === 'android' ? 'App Android' : 'Navegador'}`)),
      el('span', { class: `chip ${x.revocado ? 'critica' : 'ok'}` }, x.revocado ? 'Revocado' : 'Vinculado')),
    el('div', { class: 'nota' }, `Último uso: ${fechaHora(x.ultimo_uso)}${x.con_push ? ' · recibe avisos' : ''}`),
    el('button', { class: 'boton chico sec', type: 'button', onclick: () => accion(() => api.post(`/equipos/${x.id}/${x.revocado ? 'reactivar' : 'revocar'}`, {}), x.revocado ? 'Equipo reactivado' : 'Equipo revocado', h.recargar) },
      x.revocado ? 'Reactivar' : 'Revocar'))) : el('p', { class: 'aviso' }, 'No hay equipos vinculados.'));
}

async function hAuditoria(cuerpo) {
  const lista = await api.get('/auditoria');
  vaciar(cuerpo).append(...lista.slice(0, 120).map((a) => el('div', { class: 'tarjeta compacta' },
    el('div', { class: 'fila' }, el('strong', {}, a.accion.replace(/_/g, ' ')), el('span', { class: 'nota' }, fechaHora(a.ocurrido_en))),
    el('div', { class: 'nota' }, [a.usuario, a.entidad && `${a.entidad}${a.entidad_id ? ' ' + String(a.entidad_id).slice(0, 8) : ''}`, a.detalle && JSON.stringify(a.detalle).slice(0, 120)].filter(Boolean).join(' · ')))));
}

async function hInformes(cuerpo) {
  const hoy = new Date().toISOString().slice(0, 10), hace7 = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
  const tipo = seleccion([['completo', 'Completo (todas las hojas)'], ['mediciones', 'Mediciones'], ['alertas', 'Alertas y bitácora'], ['eventos', 'Eventos'], ['resumen', 'Resumen']], 'completo');
  const formato = seleccion([['xlsx', 'Excel (.xlsx)'], ['csv', 'CSV']], 'xlsx');
  const desde = el('input', { type: 'date', value: hace7 }), hasta = el('input', { type: 'date', value: hoy });
  vaciar(cuerpo).append(campo('Tipo', tipo), campo('Formato', formato), campo('Desde', desde), campo('Hasta', hasta),
    el('button', { class: 'boton', type: 'button', onclick: () => accion(async () => { const m = await descargar(`/informes${qs({ tipo: tipo.value, formato: formato.value, desde: desde.value, hasta: hasta.value })}`); mostrarAviso(m || 'Informe descargado', 'ok'); }) }, '⬇ Descargar informe'),
    el('p', { class: 'nota' }, 'En el celular el archivo queda en la carpeta «Descargas».'));
}

/* Cuenta */
function vCuenta() {
  const u = AQ.usuario, msg = el('div', {});
  const a = el('input', { type: 'password', placeholder: 'Contraseña actual', autocomplete: 'current-password' });
  const n = el('input', { type: 'password', placeholder: 'Nueva (12+, mayúscula, número y símbolo)', autocomplete: 'new-password' });
  vaciar(contenido).append(el('h2', {}, 'Mi cuenta'),
    el('div', { class: 'tarjeta' }, el('h3', {}, u.nombre), el('div', { class: 'nota' }, `${u.email} · ${ROL[u.rol]}`),
      el('div', { class: 'nota' }, RELE ? 'Conectado a través de Firebase (no necesita la misma red WiFi que el servidor).' : 'Conectado directamente al servidor.')),
    el('div', { class: 'tarjeta' }, el('h3', {}, 'Cambiar contraseña'), el('p', {}, a), el('p', {}, n),
      el('button', { class: 'boton sec', type: 'button', onclick: async () => { try { await api.post('/auth/password', { actual: a.value, nueva: n.value }); a.value = n.value = ''; vaciar(msg).append(el('p', { class: 'aviso ok' }, 'Contraseña actualizada.')); } catch (e) { vaciar(msg).append(el('p', { class: 'aviso error' }, mensajeError(e))); } } }, 'Actualizar'), msg),
    el('div', { id: 'equipos-zona' }),
    RELE ? null : el('p', { class: 'ayuda nota' }, el('a', { href: '/?web=1' }, 'Abrir la versión web (monitoreo completo)')),
    el('button', { class: 'boton', type: 'button', onclick: () => { clearInterval(temporizador); conocidas = null; datos = null; cerrarTodo(); cerrarSesion(); } }, 'Cerrar sesión'));
  seccionEquipos().then((x) => { const z = document.getElementById('equipos-zona'); if (z) z.append(x); });
}

iniciarSesion(raiz, 'AquaPlants', armar);
