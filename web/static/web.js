/* Web de MONITOREO (escritorio). Un dispositivo por cultivo: en todas las pantallas se ve QUÉ CULTIVO se lee y DE QUÉ DISPOSITIVO. */
const raiz = document.getElementById('app');
let contenido = null, temporizador = null, vistaActiva = 0;
const puedeReconocer = () => ['encargado_cultivo', 'supervisor', 'administrador'].includes(AQ.usuario.rol);
const etiquetaCultivo = (cultivo, disp) => `${cultivo || 'Sin cultivo'} · ${disp}`;

/*  estructura y enrutado */
function armar() {
  vaciar(raiz);
  const u = AQ.usuario, rol = u.rol;
  const tec = rol === 'tecnico';
  const enlaces = [['#/', tec ? 'Órdenes de trabajo' : 'Panel', true, tec ? 'servicio-tecnico' : 'tab-hoy'], ['#/alertas', 'Alertas', !tec, 'tab-alertas'],
    ['#/seguimiento', 'Seguimiento de alertas', !tec && rol !== 'encargado_cultivo', 'bitacora'],
    ['#/servicio', 'Servicio técnico', !tec, 'servicio-tecnico'], ['#/ubicacion', 'Ubicación', true, 'ubicacion'], ['#/estadisticas', tec ? 'Mis estadísticas' : 'Estadísticas', true, 'estadisticas'],
    ['#/comparacion', 'Comparación', !tec && rol !== 'encargado_cultivo', 'comparar'], ['#/administracion/informes', 'Informes', rol === 'administrador', 'informes'],
    ['#/simulacion', 'Simulación', rol === 'administrador', 'simular-fallas'], ['#/administracion', 'Administración', rol === 'administrador', 'parametros'], ['#/api', 'API y conexiones', rol === 'administrador', 'api'], ['#/cuenta', 'Mi cuenta', true, 'tab-cuenta']];
  contenido = el('main', { class: 'contenido' });
  raiz.append(el('div', { class: 'aplicacion' },
    el('aside', { class: 'lateral' }, el('div', { class: 'marca-fila' }, el('div', { class: 'marca-logo' }, logo('logo-lateral', true)),
      campana((n) => { AQ.abrirOrden = n.mantencion_id || null; location.hash = destinoAviso(n) === 'servicio' ? (rol === 'tecnico' ? '#/' : '#/servicio') : '#/alertas'; }, 'campana-lateral')),
      el('nav', { 'aria-label': 'Principal', id: 'menu' }, enlaces.filter((e) => e[2]).map(([h, t, , ico]) => el('a', { href: h, 'data-h': h }, ICO(ico, 'ico-nav'), t)),
        rol === 'administrador' ? el('a', { href: '/admin', class: 'externo' }, ICO('consola-django', 'ico-nav'), 'Consola Django ↗') : null),
      el('div', { class: 'usuario' }, el('strong', {}, u.nombre), el('span', {}, ROL[rol]),
        el('button', { type: 'button', class: 'boton-texto', onclick: () => { clearInterval(temporizador); cerrarSesion(); } }, ICO('salir', 'ico-btn'), 'Cerrar sesión'))),
    el('div', { class: 'columna' }, el('header', { class: 'barra-sup' }, el('span', { class: 'barra-sup-etq' }, 'Sesión iniciada como'), barraUsuario()), contenido)));
  window.removeEventListener('hashchange', enrutar);
  window.addEventListener('hashchange', enrutar);
  enrutar();
}

async function enrutar() {
  const mio = ++vistaActiva;
  AQ.editando = false;
  clearInterval(temporizador);
  const partes = (location.hash || '#/').replace(/^#\/?/, '').split('/');
  const [ruta, a, b] = partes;
  document.querySelectorAll('#menu a[data-h]').forEach((n) => n.classList.toggle('activo', (ruta === '' && n.dataset.h === '#/') || (ruta === 'administracion' && a === 'informes' ? n.dataset.h === '#/administracion/informes' : (ruta !== '' && n.dataset.h === `#/${ruta === 'instalacion' ? '' : ruta}` && ruta !== 'instalacion'))));
  const rol = AQ.usuario.rol;
  const error = (codigo) => (z) => { vaciar(z).append(pantallaErrorNumero(codigo)); };         // 403: sin permiso · 404: la sección no existe
  const vistas = {
    '': () => (rol === 'tecnico' ? vServicioTecnico(zona) : vPanel()), servicio: () => vServicioTecnico(zona), ubicacion: () => vUbicacion(zona), estadisticas: () => vEstadisticas(zona),
    instalacion: () => vInstalacion(a, b || 'estado'), alertas: () => vAlertasPagina(a), seguimiento: () => (rol === 'encargado_cultivo' ? error(403)(zona) : vSeguimiento()),
    comparacion: () => (rol === 'encargado_cultivo' ? error(403)(zona) : vComparacion()), simulacion: () => (rol === 'administrador' ? vSimulacion() : error(403)(zona)),
    administracion: () => (rol === 'administrador' ? vAdministracion(a || 'usuarios') : error(403)(zona)), cuenta: () => vCuenta(), api: () => (rol === 'administrador' ? vApi(zona) : error(403)(zona)),
  };
  const zona = el('div', {});
  vaciar(contenido).append(zona);
  const solo = { tecnico: ['', 'servicio', 'ubicacion', 'estadisticas', 'cuenta'] };            // el técnico solo ve sus solicitudes, sus equipos y su cuenta
  try {
    const r = await (solo[rol] && !solo[rol].includes(ruta) ? error(403) : (vistas[ruta] || error(404)))(zona);
    if (mio !== vistaActiva) return;
    if (r && r.refrescar) temporizador = setInterval(async () => { if (mio === vistaActiva && !document.hidden) { try { await r.refrescar(); } catch { /* reintenta */ } } }, r.cada || 5000);
  } catch (e) { if (mio === vistaActiva) vaciar(zona).append(aviso('error', mensajeError(e))); }
}
const encabezado = (titulo, ...extra) => el('header', { class: 'encabezado' }, el('h2', {}, titulo), ...extra);

/*  Panel general */
const tarjetaLectura = (l) => el('div', { class: 'lectura' }, el('span', { class: 'nm' }, l.tipo_nombre), el('strong', {}, l.valor == null ? '—' : `${fmtLectura(l.valor)} ${l.unidad}`));

/* Falla que se está atendiendo ahora (una línea, sin el proceso): al tocarla se abre esa alerta en Alertas.
   Lo solucionado o por confirmar no se muestra en el panel: queda en Alertas, en el seguimiento, en los informes y en la auditoría. */
function fallasWeb(c) {
  const f = fallasVivas(c);
  if (!f.length) return null;
  const una = f[0], mas = f.length - 1;
  return el('div', { class: 'fallas-web' },
    el('a', { class: `falla-web ${una.estado} enlace`, href: `#/alertas/${una.id}`, title: 'Ver esta alerta' }, el('strong', {}, iconoProblema(una), tituloAlerta(una)), el('span', { class: 'ver-alerta' }, ' · Ver ›')),
    mas > 0 ? el('a', { class: 'falla-web mas enlace', href: '#/alertas' }, `+${mas} más · ver todas ›`) : null);
}
function tarjetaCultivo(c, lecturas, iid, inst = null) {
  const ir = () => { location.hash = `#/instalacion/${iid}`; };
  return el('div', { class: 'cultivo', role: 'link', tabindex: '0', title: 'Ver la instalación', onclick: (e) => { if (!e.target.closest('a,button')) ir(); },
    onkeydown: (e) => { if (e.key === 'Enter' && !e.target.closest('a,button')) ir(); } },
    el('div', { class: 'cultivo-cab' }, el('h4', {}, c.nombre), estado(c.estado_conexion)),
    inst ? el('div', { class: 'cultivo-inst' }, ICO('instalaciones-kpi', 'ico-estado'), `${inst.nombre} · ${inst.codigo}`) : null,
    el('div', { class: 'suave' }, `Dispositivo ${c.dispositivo_codigo}`, fallasVivas(c).length ? el('strong', { class: 'alerta-n' }, ` · ${fallasVivas(c).length} alerta(s)`) : null),
    botonPedirTecnico({ dispositivo_id: c.dispositivo_id || null }),
    fallasWeb(c),
    el('div', { class: 'lecturas' }, lecturas.map(tarjetaLectura)));
}

async function vPanel(cont) {
  const zona = cont || contenido.firstChild;
  const cargar = async () => {
    const d = await api.get('/panel');
    const estados = await Promise.all(d.instalaciones.map((i) => api.get(`/estado-actual${qs({ instalacion_id: i.id })}`)));
    const t = d.totales, todos = d.instalaciones.flatMap((i, k) => i.cultivos.map((c) => ({ c, i, k })));
    vaciar(zona).append(
      encabezado('Panel general', botonPedirTecnico({ alTerminar: cargar }), el('span', { class: 'insignia' }, `Base de datos: ${d.modo_bd === 'firebase' ? 'Firebase Realtime Database' : 'modo local'}`)),
      el('dl', { class: 'indicadores' },
        [['Instalaciones', t.instalaciones, 'instalaciones-kpi'], ['Dispositivos en línea', `${t.en_linea} de ${t.dispositivos}`, 'dispositivos-en-linea'], ['Sin comunicación', t.sin_comunicacion, 'sin-comunicacion'],
          ['Alertas abiertas', t.alertas_abiertas, 'alertas-abiertas'], ['Mediciones (24 h)', fmt(t.mediciones_24h, 0), 'mediciones-24h']].map(([k, v, ico]) => el('div', { class: 'ind' }, ICO(ico, 'ico-kpi'), el('div', {}, el('dt', {}, k), el('dd', {}, v))))),
      todos.length ? el('div', { class: 'cultivos' }, todos.map(({ c, i, k }) => tarjetaCultivo(c, estados[k].filter((e) => e.cultivo_id === c.id), i.id, d.instalaciones.length > 1 ? i : null)))
        : d.instalaciones.length ? el('p', { class: 'ayuda' }, 'Tus instalaciones aún no tienen cultivos con dispositivo.') : null,
      !d.instalaciones.length ? el('p', { class: 'ayuda' }, 'No tienes instalaciones asignadas. Solicita acceso a un administrador.') : null);
  };
  await cargar();
  return { refrescar: cargar, cada: 10000 };
}

/* Instalación */
async function vInstalacion(id, tab) {
  const zona = contenido.firstChild;
  const insts = await api.get('/instalaciones');
  const inst = insts.find((i) => i.id === id);
  if (!inst) { vaciar(zona).append(aviso('error', 'No existe la instalación o no tienes acceso.')); return; }
  const cuerpo = el('div', {});
  const pestanas = [['estado', 'Estado actual'], ['historico', 'Histórico'], ['alertas', 'Alertas'], ['eventos', 'Eventos']];
  vaciar(zona).append(encabezado(inst.nombre), inst.descripcion ? el('p', { class: 'ayuda' }, inst.descripcion) : null,
    el('div', { class: 'pestanas', role: 'tablist' }, pestanas.map(([k, t]) => el('a', { href: `#/instalacion/${id}/${k}`, role: 'tab', class: tab === k ? 'activa' : '' }, t))), cuerpo);
  if (tab === 'estado') return pestanaEstado(cuerpo, id);
  if (tab === 'historico') return pestanaHistorico(cuerpo, id);
  if (tab === 'alertas') return listaAlertas(cuerpo, id);
  return pestanaEventos(cuerpo, id);
}

async function pestanaEstado(cuerpo, id) {
  const [cultivos, plantas, reglas] = await Promise.all([api.get(`/cultivos${qs({ instalacion_id: id })}`), api.get('/plantas'), api.get('/reglas-alerta').catch(() => [])]);
  const cargar = async () => {
    const filas = await api.get(`/estado-actual${qs({ instalacion_id: id })}`);
    vaciar(cuerpo);
    if (!cultivos.length) cuerpo.append(el('p', { class: 'ayuda' }, 'Esta instalación aún no tiene cultivos registrados.'));
    for (const c of cultivos) {
      const f = filas.filter((x) => x.cultivo_id === c.id), d = f[0];
      cuerpo.append(el('section', { class: 'bloque' },
        el('h3', {}, c.nombre, d ? el('small', {}, ` · dispositivo ${d.dispositivo_codigo} `) : null, d ? estado(d.estado_conexion) : el('small', {}, ' (sin dispositivo)')),
        c.descripcion ? el('p', { class: 'ayuda' }, c.descripcion) : null,
        el('p', { class: 'ayuda' }, 'Plantas: ', plantas.filter((p) => p.cultivo_id === c.id).map((p) => p.nombre).join(', ') || 'sin plantas registradas'),
        tabla([{ titulo: 'Variable', ver: (r) => r.tipo_nombre }, { titulo: 'Sensor', ver: (r) => `${r.codigo} · ${r.nombre}` },
          { titulo: 'Último valor', num: true, ver: (r) => (r.valor == null ? 'Sin datos' : el('strong', {}, `${fmtLectura(r.valor)} ${r.unidad}`)) },
          { titulo: 'Medido', ver: (r) => hace(r.medido_en) }, { titulo: 'Origen', ver: (r) => `${r.dispositivo_codigo} · ${ETIQUETA[r.medicion_conexion] || '—'}` },
          { titulo: 'Enlace', num: true, ver: (r) => (r.calidad_enlace == null ? '—' : `${r.calidad_enlace} %`) },
          { titulo: 'Alertas', num: true, ver: (r) => (r.alertas_abiertas ? el('strong', {}, r.alertas_abiertas) : 0) }], f, 'Este cultivo aún no tiene sensores con datos.'),
        f.length ? el('h4', { class: 'sub-graficos' }, 'Sensores · últimas 2 horas') : null,
        f.length ? el('div', { class: 'rejilla-graficos' }, f.map((r) => el('section', { class: `graf-sensor ${r.alertas_abiertas ? 'con-alerta' : ''}` },
          el('h5', {}, r.tipo_nombre, el('small', {}, ` · ${r.valor == null ? 'sin datos' : `${fmtLectura(r.valor)} ${r.unidad}`}`), r.alertas_abiertas ? el('strong', { class: 'alerta-n' }, ` · ${r.alertas_abiertas} alerta(s)`) : null),
          graficoSensor(r, reglas.filter((x) => x.activa && x.sensor_id === r.sensor_id).map((x) => ({ nombre: `${x.operador} ${x.umbral}`, valor: x.umbral })))))) : null));
    }
  };
  await cargar();
  return { refrescar: cargar, cada: 10000 };
}

const RANGOS = { '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800, '30d': 2592000 };
const TEXTO_RANGO = { '1h': 'Última hora', '6h': 'Últimas 6 horas', '24h': 'Últimas 24 horas', '7d': 'Últimos 7 días', '30d': 'Últimos 30 días' };

async function pestanaHistorico(cuerpo, id) {
  const [sensores, reglas] = await Promise.all([api.get(`/sensores${qs({ instalacion_id: id })}`), api.get('/reglas-alerta')]);
  if (!sensores.length) { cuerpo.append(el('p', { class: 'ayuda' }, 'Esta instalación aún no tiene sensores.')); return; }
  const estadoSel = { sensor: sensores[0].id, rango: '24h' };
  const grafica = el('div', {}), stats = el('div', {}), msg = el('div', {});
  const selSensor = el('select', { onchange: (e) => { estadoSel.sensor = e.target.value; dibujar(); } },
    sensores.map((s) => el('option', { value: s.id }, `${etiquetaCultivo(s.cultivo_nombre, s.dispositivo_codigo)} — ${s.tipo_nombre}`)));
  const selRango = el('select', { onchange: (e) => { estadoSel.rango = e.target.value; dibujar(); } }, Object.keys(RANGOS).map((k) => el('option', { value: k, selected: k === estadoSel.rango }, TEXTO_RANGO[k])));
  cuerpo.append(el('div', { class: 'filtros' }, el('label', { class: 'campo' }, el('span', {}, 'Cultivo · dispositivo — variable'), selSensor),
    el('label', { class: 'campo' }, el('span', {}, 'Rango'), selRango)), msg, grafica, stats);
  async function dibujar() {
    const s = sensores.find((x) => x.id === estadoSel.sensor);
    const hasta = new Date(), desde = new Date(hasta.getTime() - RANGOS[estadoSel.rango] * 1000);
    try {
      const h = await api.get(`/mediciones${qs({ sensor_id: s.id, desde: desde.toISOString(), hasta: hasta.toISOString() })}`);
      const pts = puntosDe(h), vals = pts.map((p) => p.v);
      const refs = reglas.filter((r) => r.activa && (r.sensor_id === s.id || (!r.sensor_id && r.tipo_sensor_id === s.tipo_sensor_id))).map((r) => ({ nombre: `${r.nombre}: ${r.operador} ${r.umbral}`, valor: r.umbral }));
      vaciar(msg); vaciar(grafica).append(el('h3', {}, `${s.cultivo_nombre || 'Sin cultivo'} — ${s.tipo_nombre} (${s.dispositivo_codigo} · ${s.unidad})`), grafico(pts, { unidad: s.unidad, referencias: refs, alto: 260 }));
      vaciar(stats);
      if (vals.length) stats.append(el('dl', { class: 'indicadores compactos' }, [['Mínimo', Math.min(...vals)], ['Promedio', vals.reduce((a, b) => a + b, 0) / vals.length], ['Máximo', Math.max(...vals)]]
        .map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, fmt(v, 3)))), el('div', {}, el('dt', {}, 'Puntos'), el('dd', {}, vals.length))));
    } catch (e) { vaciar(msg).append(aviso('error', e.message)); }
  }
  await dibujar();
  return { refrescar: dibujar, cada: 20000 };
}

async function pestanaEventos(cuerpo, id) {
  const datos = await api.get(`/eventos${qs({ instalacion_id: id })}`);
  cuerpo.append(tabla([{ titulo: 'Fecha', ver: (f) => fechaHora(f.ocurrido_en) }, { titulo: 'Tipo', ver: (f) => TIPO_EVENTO[f.tipo] || f.tipo },
    { titulo: 'Dispositivo', ver: (f) => f.dispositivo_codigo || '—' }, { titulo: 'Descripción', campo: 'descripcion' }], datos, 'Sin eventos registrados.'));
}

/* Alertas: cada una es una tarjeta con tres zonas: lo que pasó (pasos en cajas), lo que midió el sensor (gráfico) y la gestión. */
function tarjetaAlertaWeb(f, alTerminar, conInstalacion) {
  const dato = (k, v) => (v ? el('div', {}, el('dt', {}, k), el('dd', {}, v)) : null);
  return el('article', { id: `alerta-${f.id}`, class: `alerta-web ${f.estado} sev-${f.severidad}` },
    el('header', { class: 'alerta-web-cab' },
      el('div', { class: 'alerta-tit' }, iconoProblema(f, 'grande'), el('div', {}, el('h3', {}, tituloAlerta(f)), el('div', { class: 'suave' }, f.mensaje))),
      el('div', { class: 'alerta-web-chips' }, estado(f.estado), f.resuelta_auto ? el('span', { class: 'etiqueta auto', title: 'El valor volvió a la normalidad y no hizo falta revisarla' }, 'Automática') : null,
        f.solucion_cuestionada ? el('span', { class: 'etiqueta cuestionada', title: 'La medición no respaldó una solución informada' }, 'Solución cuestionada') : null,
        f.mantencion ? el('span', { class: `etiqueta m-${f.mantencion.estado}` }, `Mantención: ${f.mantencion.estado_texto.toLowerCase()}`) : null)),
    el('dl', { class: 'alerta-datos' }, dato('Cultivo', f.cultivo), dato('Origen', [f.dispositivo_codigo, f.sensor_codigo].filter(Boolean).join(' · ')),
      conInstalacion ? dato('Instalación', el('a', { href: `#/instalacion/${f.instalacion_id}` }, f.instalacion)) : null, dato('Creada', fechaHora(f.creada_en))),
    el('div', { class: 'alerta-web-cuerpo' },
      el('section', { class: 'alerta-col' }, el('h4', {}, 'Qué ha pasado'), pasosAlerta(f)),
      el('section', { class: 'alerta-col' }, el('h4', {}, 'Qué mostró el sensor'), graficoAlerta(f))),
    f.estado !== 'resuelta' ? el('section', { class: 'alerta-gestion' }, el('h4', {}, 'Gestión'), accionesAlerta(f, alTerminar)) : null);
}
async function listaAlertas(cuerpo, instalacionId, destacar = null) {
  const est = { filtro: 'abiertas', msg: '', destacar, yaMostrada: false };
  const tablaZona = el('div', {}), msg = el('div', {});
  const sel = el('select', { onchange: (e) => { est.filtro = e.target.value; cargar(); } },
    [['abiertas', 'Abiertas'], ['activa', 'Activas'], ['reconocida', 'Reconocidas'], ['revisada', 'Por confirmar (tickets)'], ['resuelta', 'Resueltas']].map(([v, t]) => el('option', { value: v }, t)));
  cuerpo.append(el('div', { class: 'filtros' }, el('label', { class: 'campo' }, el('span', {}, 'Estado'), sel)), msg, tablaZona);
  async function cargar(ok) {
      if (AQ.editando || ['TEXTAREA', 'SELECT', 'INPUT'].includes(document.activeElement.tagName)) return;                                  // no borrar lo que la persona está escribiendo
    let datos = await api.get(`/alertas${qs({ estado: est.filtro, instalacion_id: instalacionId })}`);
    if (est.destacar && !datos.some((f) => f.id === est.destacar)) {                       // la alerta elegida puede estar en otro estado
      for (const e of ['resuelta', 'revisada', 'reconocida', 'activa']) {
        const otras = await api.get(`/alertas${qs({ estado: e, instalacion_id: instalacionId })}`);
        if (otras.some((f) => f.id === est.destacar)) { est.filtro = e; sel.value = e; datos = otras; break; }
      }
    }
    if (est.destacar) datos = [...datos.filter((f) => f.id === est.destacar), ...datos.filter((f) => f.id !== est.destacar)];
    vaciar(msg).append(typeof ok === 'string' ? aviso('ok', ok) : null,
      est.destacar && datos.some((f) => f.id === est.destacar) ? el('p', { class: 'aviso ok' }, 'Esta es la alerta que elegiste (arriba). ', el('a', { href: '#/alertas' }, 'Ver todas las alertas')) : null);
    vaciar(tablaZona).append(datos.length ? el('div', { class: 'alertas-web' }, datos.map((f) => tarjetaAlertaWeb(f, cargar, !instalacionId)))
      : el('p', { class: 'ayuda' }, 'No hay alertas con este filtro.'));
    const elegida = est.destacar && document.getElementById(`alerta-${est.destacar}`);
    if (elegida) { elegida.classList.add('destacada'); if (!est.yaMostrada) { est.yaMostrada = true; elegida.scrollIntoView({ block: 'start' }); } }
  }
  await cargar();
  return { refrescar: cargar, cada: 10000 };

}
async function vAlertasPagina(id) { const z = contenido.firstChild; vaciar(z).append(encabezado('Alertas')); const c = el('div', {}); z.append(c); return listaAlertas(c, undefined, id || null); }

/* Comparación (supervisor y administrador) */
async function vComparacion() {
  const z = contenido.firstChild, tipos = (await api.get('/tipos-sensor')).filter((t) => t.en_uso !== false);   // solo variables que algún cultivo muestra
  const est = { variable: tipos[0] ? tipos[0].tipo_variable : '', rango: '24h', agrupar: 'cultivo' };
  const res = el('div', {});
  const selV = el('select', { onchange: (e) => { est.variable = e.target.value; cargar(); } }, tipos.map((t) => el('option', { value: t.tipo_variable }, t.nombre)));
  const selR = el('select', { onchange: (e) => { est.rango = e.target.value; cargar(); } }, [['24h', 'Últimas 24 horas'], ['7d', 'Últimos 7 días'], ['30d', 'Últimos 30 días']].map(([v, t]) => el('option', { value: v }, t)));
  const selA = el('select', { onchange: (e) => { est.agrupar = e.target.value; cargar(); } }, el('option', { value: 'cultivo' }, 'Por cultivo (un dispositivo por cultivo)'), el('option', { value: 'instalacion' }, 'Por instalación'));
  vaciar(z).append(encabezado('Comparación'), el('p', { class: 'ayuda' }, 'Compara una misma variable entre cultivos (o instalaciones). Cada gráfico usa su propia escala vertical: cada cultivo tiene sus niveles normales.'),
    el('div', { class: 'filtros' }, el('label', { class: 'campo' }, el('span', {}, 'Variable'), selV), el('label', { class: 'campo' }, el('span', {}, 'Rango'), selR),
      el('label', { class: 'campo' }, el('span', {}, 'Agrupar'), selA)), res);
  async function cargar() {
    if (!est.variable) return;
    const hasta = new Date(), desde = new Date(hasta.getTime() - { '24h': 86400, '7d': 604800, '30d': 2592000 }[est.rango] * 1000);
    try {
      const d = await api.get(`/comparacion${qs({ tipo_variable: est.variable, desde: desde.toISOString(), hasta: hasta.toISOString(), agrupar: est.agrupar })}`);
      vaciar(res).append(tabla([{ titulo: est.agrupar === 'cultivo' ? 'Cultivo · dispositivo' : 'Instalación', campo: 'nombre' }, { titulo: 'Mínimo', num: true, ver: (f) => fmt(f.minimo, 3) },
        { titulo: 'Promedio', num: true, ver: (f) => fmt(f.promedio, 3) }, { titulo: 'Máximo', num: true, ver: (f) => fmt(f.maximo, 3) }, { titulo: 'Mediciones', num: true, ver: (f) => fmt(f.n, 0) }],
        d.instalaciones, 'No hay datos de esta variable.'),
        el('div', { class: 'rejilla-graficos' }, d.instalaciones.map((i) => el('section', {}, el('h3', {}, i.nombre), grafico(i.serie.map((p) => ({ t: Date.parse(p.t), v: Number(p.prom) })), { unidad: i.unidad, alto: 190 })))));
    } catch (e) { vaciar(res).append(aviso('error', e.message)); }
  }
  await cargar();
}

/*  Mi cuenta (contraseña y segundo factor) */
async function vCuenta() {
  const z = contenido.firstChild, u = (await api.get('/auth/me')).usuario;
  AQ.usuario = { ...AQ.usuario, ...u };
  const msg = el('div', {}), msg2 = el('div', {}), zona2fa = el('div', {});
  const f = { actual: '', nueva: '', repetir: '' };
  const cambiar = el('form', { class: 'panel-form' }, msg,
    ...[['actual', 'Contraseña actual'], ['nueva', 'Contraseña nueva'], ['repetir', 'Repetir contraseña nueva']].map(([k, t]) =>
      el('label', { class: 'campo' }, el('span', {}, t), el('input', { type: 'password', required: true, autocomplete: k === 'actual' ? 'current-password' : 'new-password', oninput: (e) => { f[k] = e.target.value; } }))),
    el('button', { class: 'boton', type: 'submit' }, 'Guardar contraseña'));
  cambiar.addEventListener('submit', async (e) => {
    e.preventDefault(); vaciar(msg);
    if (f.nueva !== f.repetir) return msg.append(aviso('error', 'Las contraseñas nuevas no coinciden.'));
    try { await api.post('/auth/password', { actual: f.actual, nueva: f.nueva }); cambiar.reset(); msg.append(aviso('ok', 'Contraseña actualizada. Se cerraron tus otras sesiones.')); }
    catch (err) { msg.append(aviso('error', mensajeError(err))); }
  });
  function pintar2fa(activo, cfg) {
    vaciar(zona2fa);
    const codigo = el('input', { type: 'text', inputmode: 'numeric', pattern: '\\d{6}', maxlength: 6, required: true, autocomplete: 'one-time-code' });
    if (!activo && !cfg) zona2fa.append(el('p', { class: 'ayuda' }, 'Agrega un código temporal de una aplicación autenticadora para proteger tu cuenta.'),
      el('button', { class: 'boton', type: 'button', onclick: async () => { try { vaciar(msg2); pintar2fa(false, await api.post('/auth/2fa/iniciar')); } catch (e) { msg2.append(aviso('error', e.message)); } } }, 'Configurar segundo factor'));
    else if (!activo) {
      const form = el('form', {}, el('p', {}, 'Ingresa esta clave en tu aplicación autenticadora (tipo de clave: basada en tiempo):'), el('p', {}, el('code', { class: 'secreto' }, cfg.secreto)),
        el('p', { class: 'ayuda' }, 'También puedes usar este enlace: ', el('code', { class: 'secreto' }, cfg.uri)), el('label', { class: 'campo' }, el('span', {}, 'Código de 6 dígitos'), codigo),
        el('button', { class: 'boton', type: 'submit' }, 'Activar'));
      form.addEventListener('submit', async (e) => { e.preventDefault(); try { await api.post('/auth/2fa/activar', { codigo: codigo.value }); vaciar(msg2).append(aviso('ok', 'Segundo factor activado.')); pintar2fa(true); } catch (err) { vaciar(msg2).append(aviso('error', err.message)); } });
      zona2fa.append(form);
    } else {
      const pass = el('input', { type: 'password', required: true, autocomplete: 'current-password' });
      const form = el('form', {}, el('p', {}, 'El segundo factor está ', el('strong', {}, 'activo'), '. Para desactivarlo confirma tu contraseña y un código vigente.'),
        el('label', { class: 'campo' }, el('span', {}, 'Contraseña'), pass), el('label', { class: 'campo' }, el('span', {}, 'Código de 6 dígitos'), codigo),
        el('button', { class: 'boton-secundario', type: 'submit' }, 'Desactivar'));
      form.addEventListener('submit', async (e) => { e.preventDefault(); try { await api.post('/auth/2fa/desactivar', { password: pass.value, codigo: codigo.value }); vaciar(msg2).append(aviso('ok', 'Segundo factor desactivado.')); pintar2fa(false); } catch (err) { vaciar(msg2).append(aviso('error', err.message)); } });
      zona2fa.append(form);
    }
  }
  pintar2fa(!!u.totpActivo);
  vaciar(z).append(encabezado('Mi cuenta'), el('dl', { class: 'ficha' }, [['Nombre', u.nombre], ['Correo', u.email], ['Rol', ROL[u.rol]]].map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, v)))),
    el('section', { class: 'panel-form' }, el('h3', {}, 'Cambiar contraseña'), el('p', { class: 'ayuda' }, 'Mínimo 12 caracteres, con mayúscula, minúscula, número y símbolo.'), cambiar),
    el('section', { class: 'panel-form' }, el('h3', {}, 'Segundo factor de autenticación'), msg2, zona2fa), await seccionEquipos());
}

/*  Simulación (administrador) */
async function vSimulacion() {
  const z = contenido.firstChild;
  const cargar = async () => {
    const s = await api.get('/simulacion');
    const ejecutar = async (accion, extra, ok) => { try { await api.post('/simulacion/accion', { accion, ...(extra || {}) }); if (ok) mostrarAviso(ok, 'ok'); await cargar(); } catch (e) { mostrarAviso(mensajeError(e), 'error'); } };
    const etiqueta = Object.fromEntries((s.escenarios || []).map((e) => [e.codigo, e.etiqueta]));
    if (!vSimulacion.disp || !s.dispositivos.some((d) => d.codigo === vSimulacion.disp)) vSimulacion.disp = s.dispositivos[0] && s.dispositivos[0].codigo;
    const actual = (s.dispositivos.find((d) => d.codigo === vSimulacion.disp) || {}).escenario;
    if (document.activeElement && document.activeElement.id === 'sim-disp') return;
    if (!s.disponible) { vaciar(z).append(encabezado('Simulación'), aviso('info', 'La simulación no está activa en este servidor (AQ_SIMULACION=0 o dispositivos reales conectados).')); return; }
    const opcion = (accion, texto, valor) => el('label', { class: 'campo-check' }, el('input', { type: 'checkbox', checked: valor, onchange: () => ejecutar(accion) }), ' ', texto);
    vaciar(z).append(encabezado('Simulación de dispositivos AquaBrain'),
      el('p', { class: 'ayuda' }, 'Cada cultivo tiene su dispositivo virtual. Envían mediciones firmadas al mismo endpoint que usaría un dispositivo físico; para pasar a hardware real solo se pausa la simulación.'),
      el('section', { class: 'panel-form' }, el('h3', {}, 'Provocar una falla del cultivo'),
        el('p', { class: 'ayuda' }, 'La alerta aparece en segundos y la falla se mantiene hasta que alguien registra «Sí, quedó solucionado» (o se termina aquí). «Sin comunicación» aparece cuando pasan los minutos del parámetro correspondiente.'),
        el('label', { class: 'campo' }, el('span', {}, 'Cultivo / dispositivo'), el('select', { id: 'sim-disp', onchange: (e) => { vSimulacion.disp = e.target.value; e.target.blur(); cargar(); } },
          s.dispositivos.map((d) => el('option', { value: d.codigo, selected: d.codigo === vSimulacion.disp }, `${d.cultivo} · ${d.codigo}`)))),
        actual ? el('p', { class: 'sim-activa' }, ICO(ICONO_ESCENARIO[actual] || 'alerta-detectada-c', 'ico-btn'), `Falla activa en este equipo: ${etiqueta[actual] || actual}`) : el('p', { class: 'ayuda' }, 'Este equipo funciona con normalidad: elige una falla para provocarla.'),
        el('div', { class: 'botones-escenario' }, (s.escenarios || []).map((e) => botonEscenario(e, actual === e.codigo, 'boton-secundario', !s.activa,
          () => ejecutar('escenario', { escenario: e.codigo, dispositivo: vSimulacion.disp }, `Falla provocada: ${e.etiqueta}`))),
          el('button', { type: 'button', class: 'boton', onclick: () => ejecutar('reparar', { dispositivo: vSimulacion.disp }, 'Falla terminada') }, ICO('terminar-falla', 'ico-btn'), 'Terminar la falla'))),
      el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: () => ejecutar(s.activa ? 'pausar' : 'reanudar') }, s.activa ? 'Pausar simulación' : 'Reanudar simulación'),
        el('button', { class: 'boton-secundario', type: 'button', disabled: !s.activa, title: 'Una falla al azar en cada cultivo: genera alertas', onclick: () => ejecutar('anomalia', null, 'Fallas al azar provocadas') }, 'Falla al azar en todos'),
        el('button', { class: 'boton-secundario', type: 'button', disabled: !s.activa, title: 'Los dispositivos guardan en cola local y sincronizan al volver', onclick: () => ejecutar('corte') }, 'Cortar la red (45 s)')),
      el('section', { class: 'panel-form' }, el('h3', {}, 'Comportamiento automático'), opcion('alternar_anomalias', 'Fallas al azar de vez en cuando', s.opciones.anomalias),
        opcion('alternar_datos_erroneos', 'Datos erróneos ocasionales (la API los rechaza)', s.opciones.datos_erroneos), opcion('alternar_cortes', 'Cortes de red automáticos', s.opciones.cortes_automaticos)),
      tabla([{ titulo: 'Cultivo', ver: (d) => d.cultivo || '—' }, { titulo: 'Dispositivo', campo: 'codigo' }, { titulo: 'Instalación', campo: 'sistema' },
        { titulo: 'Falla actual', ver: (d) => (d.escenario ? el('span', { class: 'etiqueta activa' }, etiqueta[d.escenario] || d.escenario) : 'Normal') },
        { titulo: 'Red', ver: (d) => (d.red_caida ? 'Sin conexión (cola local)' : 'Conectado') }, { titulo: 'En cola', num: true, campo: 'en_cola' }, { titulo: 'Generadas', num: true, campo: 'generadas' },
        { titulo: 'Aceptadas', num: true, campo: 'aceptadas' }, { titulo: 'Rechazadas', num: true, campo: 'rechazadas' }, { titulo: 'Duplicadas', num: true, campo: 'duplicadas' }], s.dispositivos));
  };
  await cargar();
  return { refrescar: cargar, cada: 8000 };
}

iniciarSesion(raiz, 'Ingreso a AquaPlants', armar);
