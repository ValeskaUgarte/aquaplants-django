/* Servicio técnico, ubicación de los dispositivos y estadísticas. Las usan la web y la app móvil: cada función recibe el contenedor donde dibuja. */

/*  Mapa (Google Maps con la clave del administrador; si no hay clave, un mapa incrustado simple)  */
let _claveMapa;                                         // undefined = aún no consultada
async function claveMapa() {
  if (_claveMapa === undefined) { try { _claveMapa = (await api.get('/config-mapa')).google_key || ''; } catch { _claveMapa = ''; } }
  return _claveMapa;
}
let _cargandoMapas = null;
function cargarGoogleMaps(clave) {
  if (window.google && window.google.maps) return Promise.resolve();
  if (_cargandoMapas) return _cargandoMapas;
  _cargandoMapas = new Promise((ok, mal) => {
    window.__aqMapaListo = ok;
    window.gm_authFailure = () => { _claveMapa = ''; document.querySelectorAll('.mapa-caja').forEach((c) => c.dispatchEvent(new CustomEvent('mapa-error'))); };
    const s = document.createElement('script');
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(clave)}&callback=__aqMapaListo&loading=async`;
    s.async = true; s.onerror = () => { _cargandoMapas = null; mal(new Error('No se pudo cargar Google Maps (¿sin internet?).')); };
    document.head.append(s);
  });
  return _cargandoMapas;
}
const COLOR_ESTADO = { en_linea: '#1b8a4b', sin_comunicacion: '#c62828', sin_datos: '#8a97a0' };
const TEXTO_ESTADO = { en_linea: 'En línea', sin_comunicacion: 'Sin comunicación', sin_datos: 'Sin datos' };

/* Dibuja los puntos [{id, titulo, detalle, lat, lng, estado}] y devuelve {elegir(id), trayecto(puntos)} para mover el mapa desde fuera. */
function mapaDispositivos(caja, puntos, { alto = 380, alElegir } = {}) {
  const ctl = { elegir() {}, trayecto() {} };
  vaciar(caja).classList.add('mapa-caja');
  if (!puntos.length) { caja.append(el('div', { class: 'graf-vacio' }, 'Ningún dispositivo ha informado su posición todavía.')); return ctl; }
  const fallback = () => {                                // sin clave: mapa incrustado de un dispositivo a la vez
    vaciar(caja);
    const marco = el('iframe', { class: 'mapa-marco', title: 'Mapa', loading: 'lazy', referrerpolicy: 'no-referrer' }), nota = el('p', { class: 'ayuda' });
    marco.style.height = `${alto}px`;
    const ir = (p) => { marco.src = `https://www.google.com/maps?q=${p.lat},${p.lng}&z=16&output=embed`; nota.textContent = `${p.titulo} · ${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`; };
    ir(puntos[0]); ctl.elegir = (id) => { const p = puntos.find((x) => x.id === id); if (p) ir(p); };
    caja.append(marco, nota, el('p', { class: 'suave' }, 'Para ver todos los dispositivos en un mismo mapa, el administrador debe poner la clave de Google Maps en Administración → Parámetros.'));
  };
  claveMapa().then(async (clave) => {
    if (!clave) return fallback();
    try {
      await cargarGoogleMaps(clave);
      vaciar(caja); const div = el('div', { class: 'mapa-g' }); div.style.height = `${alto}px`; caja.append(div);
      caja.addEventListener('mapa-error', () => { fallback(); }, { once: true });
      const g = window.google.maps, mapa = new g.Map(div, { center: { lat: puntos[0].lat, lng: puntos[0].lng }, zoom: 15, mapTypeControl: true, streetViewControl: false }), limites = new g.LatLngBounds(), info = new g.InfoWindow();
      const marcas = new Map(); let linea = null;
      for (const p of puntos) {
        const m = new g.Marker({ position: { lat: p.lat, lng: p.lng }, map: mapa, title: p.titulo, icon: { path: g.SymbolPath.CIRCLE, scale: 10, fillColor: COLOR_ESTADO[p.estado] || '#8a97a0', fillOpacity: 1, strokeColor: '#fff', strokeWeight: 2 } });
        m.addListener('click', () => { info.setContent(el('div', { class: 'info-mapa' }, el('strong', {}, p.titulo), el('div', {}, p.detalle || ''))); info.open(mapa, m); if (alElegir) alElegir(p.id); });
        marcas.set(p.id, m); limites.extend(m.getPosition());
      }
      if (puntos.length > 1) mapa.fitBounds(limites, 60);
      ctl.elegir = (id) => { const m = marcas.get(id); if (m) { mapa.panTo(m.getPosition()); mapa.setZoom(17); g.event.trigger(m, 'click'); } };
      ctl.trayecto = (pts) => {
        if (linea) linea.setMap(null);
        if (!pts.length) return;
        linea = new g.Polyline({ path: pts.map((p) => ({ lat: p.lat, lng: p.lng })), geodesic: true, strokeColor: '#1565c0', strokeOpacity: 0.85, strokeWeight: 3, map: mapa });
        const b = new g.LatLngBounds(); pts.forEach((p) => b.extend({ lat: p.lat, lng: p.lng })); mapa.fitBounds(b, 60);
      };
    } catch (e) { fallback(); }
  });
  return ctl;
}

/* Ubicación de los dispositivos  */
async function vUbicacion(zona) {
  const jefe = ['administrador', 'supervisor'].includes(AQ.usuario.rol), lista = await api.get('/ubicaciones');
  const conGps = lista.filter((d) => d.ubicacion), caja = el('div', {}), tarjetas = el('div', { class: 'lista-ubic' });
  const puntos = conGps.map((d) => ({ id: d.id, titulo: `${d.cultivo || 'Sin cultivo'} · ${d.codigo}`, detalle: `${d.instalacion || ''} — ${TEXTO_ESTADO[d.estado_conexion] || ''}`, lat: d.ubicacion.lat, lng: d.ubicacion.lng, estado: d.estado_conexion }));
  const mapa = mapaDispositivos(caja, puntos, { alto: 400 });
  const trayectoTxt = el('div', {});
  const tarjeta = (d) => {
    const u = d.ubicacion, msg = el('div', {});
    const corregir = () => {
      const lat = el('input', { type: 'text', inputmode: 'decimal', placeholder: 'Latitud (ej. -33.4489)', value: u ? u.lat : '' }), lng = el('input', { type: 'text', inputmode: 'decimal', placeholder: 'Longitud (ej. -70.6693)', value: u ? u.lng : '' });
      const guardar = async () => {
        try { await api.patch(`/dispositivos/${d.id}/ubicacion`, { lat: Number(lat.value.replace(',', '.')), lng: Number(lng.value.replace(',', '.')) }); mostrarAviso('Ubicación actualizada', 'ok'); await vUbicacionRecargar(); }
        catch (e) { vaciar(msg).append(aviso('error', mensajeError(e))); }
      };
      vaciar(msg).append(el('div', { class: 'derivar-form' }, el('strong', {}, 'Indicar dónde está el equipo'), lat, lng,
        el('p', { class: 'suave' }, 'Úsalo si el equipo no tiene GPS o no hay señal. Apenas el GPS vuelva a informar, manda el GPS.'),
        el('div', { class: 'botones-revision' }, el('button', { type: 'button', class: 'boton-secundario boton-ok', onclick: guardar }, 'Guardar'), el('button', { type: 'button', class: 'boton-secundario', onclick: () => vaciar(msg) }, 'Cancelar'))));
    };
    const ver = async () => {
      mapa.elegir(d.id);
      try { const t = await api.get(`/dispositivos/${d.id}/trayecto`); mapa.trayecto(t.puntos);
        vaciar(trayectoTxt).append(el('p', { class: 'ayuda' }, t.puntos.length > 1 ? `Recorrido de ${d.codigo}: ${t.puntos.length} posiciones registradas, de ${fechaHora(t.puntos[0].en)} a ${fechaHora(t.puntos[t.puntos.length - 1].en)}.` : `${d.codigo} no se ha movido: una sola posición registrada.`));
      } catch (e) { vaciar(trayectoTxt).append(aviso('error', mensajeError(e))); }
    };
    return el('article', { class: 'card-informe ubic' },
      el('div', { class: 'mantencion-cab' }, el('strong', {}, `${d.cultivo || 'Sin cultivo'} · ${d.codigo}`), el('span', { class: 'chip-est', 'data-e': d.estado_conexion }, TEXTO_ESTADO[d.estado_conexion] || d.estado_conexion)),
      el('div', { class: 'meta suave' }, [d.instalacion, d.direccion].filter(Boolean).join(' · '), (d.responsables || []).length ? ` · A cargo: ${d.responsables.join(', ')}` : ''),
      u ? el('div', { class: 'meta' }, `📍 ${u.lat.toFixed(5)}, ${u.lng.toFixed(5)}${u.precision ? ` (±${fmt(u.precision, 0)} m)` : ''} · ${u.origen === 'manual' ? 'indicada a mano' : 'GPS del dispositivo'} · ${hace(u.actualizada_en)}`)
        : el('div', { class: 'meta suave' }, 'Sin posición todavía.'),
      el('div', { class: 'botones-revision' }, u ? el('button', { type: 'button', class: 'boton-secundario', onclick: ver }, 'Ver en el mapa y su recorrido') : null,
        u ? el('a', { class: 'boton-texto', href: enlaceMapa(u), target: '_blank', rel: 'noopener' }, 'Abrir en Google Maps') : null,
        u ? el('a', { class: 'boton-texto', href: enlaceRuta(u), target: '_blank', rel: 'noopener' }, 'Cómo llegar') : null,
        jefe ? el('button', { type: 'button', class: 'boton-texto', onclick: corregir }, 'Corregir ubicación') : null),
      botonPedirTecnico({ dispositivo_id: d.id }), msg);
  };
  tarjetas.append(lista.map(tarjeta));
  const vUbicacionRecargar = () => vUbicacion(zona);
  vaciar(zona).append(el('p', { class: 'ayuda' }, 'Dónde está cada dispositivo según su GPS. Si un equipo se mueve, aquí se ve su última posición y su recorrido.'), caja, trayectoTxt,
    lista.length ? tarjetas : el('p', { class: 'ayuda' }, 'No hay dispositivos para mostrar.'));
}

/*  Solicitudes técnicas: la ve el técnico (lo suyo y la bolsa) y quienes las piden  */
/*  API y conexiones (solo administrador): cómo viajan los datos, con el estado en vivo y, aparte, la lista técnica  */
async function vApi(zona) {
  const d = await api.get('/documentacion'), fb = d.fb || d.firebase, e = d.estado, enNube = fb.modo === 'firebase';
  const paso = (icono, titulo, texto, estado, ok = true) => el('div', { class: 'flujo-paso' }, ICO(icono, 'ico-flujo'), el('strong', {}, titulo), el('p', {}, texto),
    el('span', { class: `flujo-estado ${ok ? 'ok' : 'aviso'}` }, estado));
  const flecha = () => el('div', { class: 'flujo-flecha', 'aria-hidden': 'true' }, '→');
  const pasos = [
    paso('sensores', 'Sensores', 'Miden agua, nutrientes, pH, luz y temperatura en cada cultivo.', `${e.dispositivos} equipo${e.dispositivos === 1 ? '' : 's'} instalado${e.dispositivos === 1 ? '' : 's'}`),
    paso('dispositivos', 'Equipo AquaBrain', 'Reúne las mediciones y las envía por internet, firmadas para que nadie las falsifique.', `${e.en_linea} de ${e.dispositivos} en línea`, e.sin_comunicacion === 0),
    paso('api', 'API', 'Es la puerta de entrada: recibe los datos y responde cuando la web o la app piden información.', 'Funcionando'),
    paso('consola-django', 'Servidor Django', 'Revisa cada dato, detecta fallas, crea las alertas y avisa a las personas indicadas.', `${e.alertas_abiertas} alerta${e.alertas_abiertas === 1 ? '' : 's'} abierta${e.alertas_abiertas === 1 ? '' : 's'}`),
    paso('firebase', 'Base de datos Firebase', 'Guarda el historial de mediciones, alertas y auditoría.', enNube ? `Nube${fb.proyecto ? ` · ${fb.proyecto}` : ''}` : 'Modo local (sin internet)', true),
    paso('tab-hoy', 'Web y app móvil', 'Muestran el estado en vivo, las alertas y las visitas técnicas a cada persona según su rol.', `${fmt(e.mediciones_24h, 0)} mediciones en 24 h`)];
  const lista = el('div', { class: 'flujo' });
  pasos.forEach((p, i) => { if (i) lista.append(flecha()); lista.append(p); });

  // Panel en vivo: datos reales que la API entrega en /api/panel, actualizados cada 5 segundos
  const kpis = el('div', { class: 'kpis' });
  const cultivos = el('div', { class: 'vivo-cultivos' });
  const pintar = (p) => {
    const t = p.totales;
    vaciar(kpis).append(kpi('Instalaciones', t.instalaciones, null, null), kpi('Dispositivos en línea', `${t.en_linea} de ${t.dispositivos}`, null, null),
      kpi('Sin comunicación', t.sin_comunicacion, null, null), kpi('Alertas abiertas', t.alertas_abiertas, null, null));
    vaciar(cultivos).append(...p.instalaciones.flatMap((i) => i.cultivos.map((c) => el('article', { class: 'vivo-cultivo' },
      el('h4', {}, c.nombre), el('p', { class: 'suave' }, `${i.nombre} · ${c.dispositivo_codigo}`),
      el('p', {}, `Conexión: ${c.estado_conexion === 'en_linea' ? 'en línea' : c.estado_conexion === 'sin_comunicacion' ? 'sin comunicación' : 'sin datos'}`),
      el('p', {}, `Alertas abiertas: ${c.alertas_abiertas}`)))));
  };
  // Selector: eliges una ruta de lectura y ves su petición y su respuesta real
  const rutasLectura = (d.grupos || []).flatMap((g) => g.rutas
    .filter((r) => r.metodo === 'GET' && (r.ejemplo === '' || r.ejemplo === '?instalacion_id=')));
  const seleccion = el('select', { class: 'api-selector', onchange: () => mostrar() },
    rutasLectura.map((r) => el('option', { value: r.ruta, 'data-inst': r.ejemplo === '?instalacion_id=' ? '1' : '' }, r.ruta)));
  const linea = el('div', { class: 'api-linea' }, 'Elige una ruta para ver su respuesta.');
  const crudo = el('pre', { class: 'api-json' }, '');
  const mostrar = async () => {
    const opt = seleccion.selectedOptions[0];
    if (!opt) return;
    const ruta = opt.value;
    let consulta = ruta.replace(/^\/api/, '');
    const t0 = performance.now();
    try {
      if (opt.dataset.inst) {
        const lista = await api.get('/instalaciones');
        if (!lista.length) throw new Error('No hay instalaciones registradas.');
        consulta += `?instalacion_id=${lista[0].id}`;
      }
      const datos = await api.get(consulta);
      const ms = Math.round(performance.now() - t0);
      linea.textContent = `GET ${ruta} · 200 OK · ${ms} ms · ${new Date().toLocaleTimeString('es-CL')}`;
      crudo.textContent = JSON.stringify(datos, null, 2).slice(0, 6000);
    } catch (err) {
      linea.textContent = `GET ${ruta} · error`;
      crudo.textContent = mensajeError(err);
    }
  };
  seleccion.value = '/api/panel';
  const cargar = async () => { pintar(await api.get('/panel')); await mostrar(); };
  await cargar();

  vaciar(zona).append(encabezado('Cómo se conecta el sistema'),
    el('p', { class: 'ayuda' }, 'Los datos viajan de izquierda a derecha, de los sensores hasta la pantalla de cada persona. La API es la puerta por donde entran y salen.'), lista,
    el('section', { class: 'bloque' }, el('h3', {}, 'Datos en vivo desde la API'),
      el('p', { class: 'suave' }, 'Elige una ruta de la API. El panel se actualiza solo cada 5 segundos.'), el('div', { class: 'api-barra' }, el('label', {}, 'Ruta de la API: '), seleccion), linea, crudo, kpis, cultivos),
        el('section', { class: 'api-fb' }, ICO('firebase', 'ico-api'), el('div', {}, el('h3', {}, 'Base de datos Firebase'),
          el('p', {}, enNube ? `Conectada a la nube${fb.proyecto ? ` (proyecto «${fb.proyecto}»)` : ''}.` : 'Hoy el sistema guarda todo dentro de este servidor, sin internet. Para usar la nube de Firebase se configura una sola vez en el archivo .env del servidor.'),
          el('p', { class: 'suave' }, 'Es seguro conectarla mientras las reglas de la base bloqueen el acceso directo y la clave se quede solo en el servidor.'),
          fb.consola ? el('a', { class: 'boton-secundario', href: fb.consola, target: '_blank', rel: 'noopener' }, ICO('firebase', 'ico-btn'), 'Abrir la consola de Firebase') : null)),);
  return { refrescar: cargar, cada: 5000 };
}

/*  Órdenes de trabajo del técnico: lo nuevo (asignado o en la bolsa), lo que está haciendo y lo ya cerrado  */
async function vOrdenesTecnico(zona) {
  const kp = el('div', { class: 'kpis ot-kpis' }), seg = el('div', { class: 'segmento ot-seg' }), lista = el('div', { class: 'lista-ot' });
  let todas = [];
  const inicioHoy = new Date(); inicioHoy.setHours(0, 0, 0, 0);
  const grupos = () => ({ nuevas: todas.filter((m) => m.estado === 'pendiente'), curso: todas.filter((m) => m.estado === 'en_curso'),
    historial: todas.filter((m) => ['completada', 'cancelada'].includes(m.estado)) });
  const VACIO = { nuevas: 'No hay solicitudes nuevas. Cuando te asignen una o llegue una a la bolsa, te avisamos con una notificación.',
    curso: 'No tienes visitas en curso. Acepta una solicitud nueva para empezar.', historial: 'Todavía no has cerrado ninguna visita.' };
  const pintar = () => {
    const g = grupos(), hechasHoy = g.historial.filter((m) => m.estado === 'completada' && m.completada_en && new Date(m.completada_en) >= inicioHoy).length;
    if (AQ.abrirOrden) { const o = todas.find((m) => m.id === AQ.abrirOrden); if (o) zona.dataset.seg = o.estado === 'pendiente' ? 'nuevas' : o.estado === 'en_curso' ? 'curso' : 'historial'; }
    if (!zona.dataset.seg) zona.dataset.seg = g.curso.length ? 'curso' : g.nuevas.length ? 'nuevas' : 'historial';
    const actual = zona.dataset.seg, items = g[actual];
    vaciar(kp).append(kpi('Nuevas', g.nuevas.length, 'por aceptar', 'pendiente'), kpi('En curso', g.curso.length, 'tus visitas', 'en-revision'), kpi('Cerradas hoy', hechasHoy, null, 'resueltas'));
    vaciar(seg).append(...[['nuevas', 'Nuevas'], ['curso', 'En curso'], ['historial', 'Historial']].map(([id, t]) =>
      el('button', { type: 'button', class: actual === id ? 'activo' : '', onclick: () => { zona.dataset.seg = id; AQ.editando = false; pintar(); } }, `${t} (${g[id].length})`)));
    vaciar(lista).append(items.length ? items.slice(0, actual === 'historial' ? 30 : 100).map((m) => ordenTrabajo(m, () => { AQ.abrirOrden = m.id; return cargar(true); })) : el('p', { class: 'ayuda ot-vacio' }, VACIO[actual]));
    if (AQ.abrirOrden) { const nodo = document.getElementById(`ot-${AQ.abrirOrden}`); if (nodo) { nodo.classList.add('resaltada'); nodo.scrollIntoView({ block: 'start' }); } AQ.abrirOrden = null; }
  };
  const cargar = async (forzar) => {
    if (!forzar && (AQ.editando || /^(TEXTAREA|SELECT|INPUT)$/.test((document.activeElement || {}).tagName || '') || zona.querySelector('details[open]'))) return;
    todas = await api.get('/mantenciones'); pintar();
  };
  vaciar(zona).append(el('p', { class: 'ayuda' }, 'Tus órdenes de trabajo: acepta una solicitud, ve al equipo con «Cómo llegar», avisa cuando llegues y cierra con tu informe. Si no puedes, devuélvela a la bolsa con el motivo.'), kp, seg, lista);
  await cargar(true);
  return { refrescar: () => cargar(false), cada: 15000 };
}

async function vServicioTecnico(zona) {
  if (AQ.usuario.rol === 'tecnico') return vOrdenesTecnico(zona);
  const rol = AQ.usuario.rol, esTec = rol === 'tecnico', puedePedir = ['encargado_cultivo', 'supervisor', 'administrador'].includes(rol);
  const filtro = zona.dataset.filtro || 'abiertas', lista = el('div', { class: 'lista-mantenciones' });
  const sel = el('select', { onchange: (e) => { zona.dataset.filtro = e.target.value; cargar(true); } }, [['abiertas', 'Abiertas (pendientes y en curso)'], ['', 'Todas'], ['completada', 'Realizadas'], ['cancelada', 'Canceladas']]
    .map(([v, t]) => el('option', { value: v, selected: v === filtro }, t)));
  const cargar = async (forzar) => {
    if (!forzar && (AQ.editando || /^(TEXTAREA|SELECT|INPUT)$/.test((document.activeElement || {}).tagName || ''))) return;
    const datos = await api.get(`/mantenciones${qs({ estado: zona.dataset.filtro || 'abiertas' })}`);
    vaciar(lista).append(datos.length ? datos.map((m) => el('article', { class: 'card-informe' }, cajaMantencion(m, () => cargar(true)))) : el('p', { class: 'ayuda' },
      esTec ? 'No tienes solicitudes. Cuando te asignen una, o llegue una a la bolsa, aparecerá aquí con aviso.' : 'No hay solicitudes con este filtro.'));
  };
  vaciar(zona).append(el('p', { class: 'ayuda' }, esTec ? 'Solicitudes que te asignaron y las que están en la bolsa (sin técnico). La primera que aceptes es tuya; si no puedes, devuélvela con el motivo.'
      : 'Pedidos de arreglo, reemplazo, retiro o mantención de los dispositivos, con su estado y quién los atiende.'),
    el('div', { class: 'barra-filtros' }, el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Mostrar'), sel),
      botonPedirTecnico({ alTerminar: () => cargar(true) }, 'boton-secundario boton-tec', 'Pedir técnico')), lista);
  await cargar(true);
  return { refrescar: () => cargar(false), cada: 15000 };
}

/*  Estadísticas  */
const kpi = (titulo, valor, nota, icono) => el('div', { class: 'kpi' }, icono ? ICO(icono, 'ico-kpi') : null, el('span', { class: 'kpi-tit' }, titulo), el('strong', {}, valor == null ? '—' : valor), nota ? el('small', { class: 'suave' }, nota) : null);
const minTxt = (v) => (v == null ? null : v < 1 ? '< 1 min' : v < 60 ? `${fmt(v, 0)} min` : v < 1440 ? `${Math.floor(v / 60)} h ${Math.round(v % 60)} min` : `${fmt(v / 1440, 1)} días`);
function barrasH(items, vacio = 'Sin datos en este período.') {
  if (!items.length) return el('p', { class: 'suave' }, vacio);
  const max = Math.max(...items.map((i) => i.n));
  return el('div', { class: 'barras-h' }, items.map((i) => el('div', { class: 'fila-barra' }, el('span', { class: 'etq', title: i.nombre }, i.nombre),
    el('span', { class: 'pista' }, el('span', { class: 'relleno', style: { width: `${Math.max(3, (i.n / max) * 100)}%` } })), el('b', {}, i.n))));
}
function graficoDias(serie) {                                         // barras por día: alertas nuevas y resueltas
  const W = 720, H = 200, m = { i: 30, d: 8, a: 10, b: 26 }, max = Math.max(1, ...serie.flatMap((s) => [s.alertas, s.resueltas])), ancho = (W - m.i - m.d) / serie.length;
  const ns = 'http://www.w3.org/2000/svg', n = (t, a, txt) => { const e = document.createElementNS(ns, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); if (txt != null) e.textContent = txt; return e; };
  const svg = n('svg', { viewBox: `0 0 ${W} ${H}`, class: 'grafico', role: 'img', 'aria-label': 'Alertas por día' });
  for (let i = 0; i <= 4; i++) { const y = m.a + (H - m.a - m.b) * (1 - i / 4); svg.append(n('line', { x1: m.i, x2: W - m.d, y1: y, y2: y, class: 'g-rejilla' }), n('text', { x: m.i - 4, y: y + 3, 'text-anchor': 'end', class: 'g-texto' }, String(Math.round((max * i) / 4)))); }
  serie.forEach((s, k) => {
    const x = m.i + k * ancho, alto = (v) => ((H - m.a - m.b) * v) / max;
    svg.append(n('rect', { x: x + ancho * 0.1, width: ancho * 0.38, y: H - m.b - alto(s.alertas), height: alto(s.alertas), class: 'barra-a' }), n('rect', { x: x + ancho * 0.5, width: ancho * 0.38, y: H - m.b - alto(s.resueltas), height: alto(s.resueltas), class: 'barra-r' }));
    if (serie.length <= 14 || k % Math.ceil(serie.length / 10) === 0) svg.append(n('text', { x: x + ancho / 2, y: H - 8, 'text-anchor': 'middle', class: 'g-texto' }, s.dia.slice(5)));
  });
  return el('figure', { class: 'graf-fig' }, svg, el('figcaption', { class: 'graf-pie' }, el('span', { class: 'lg a' }, '■'), ' Alertas nuevas   ', el('span', { class: 'lg r' }, '■'), ' Alertas resueltas'));
}
async function vEstadisticas(zona) {
  const dias = Number(zona.dataset.dias || 30), e = await api.get(`/estadisticas${qs({ dias })}`), m = e.mantenciones;
  const selDias = el('select', { onchange: (ev) => { zona.dataset.dias = ev.target.value; vEstadisticas(zona); } }, [7, 30, 90, 180].map((d) => el('option', { value: d, selected: d === dias }, `Últimos ${d} días`)));
  const bloqueMant = el('section', { class: 'card-informe' }, el('h4', {}, 'Servicio técnico'),
    el('div', { class: 'kpis' }, kpi('Solicitudes', m.total, null, 'visita-tecnico'), kpi('Abiertas ahora', m.abiertas, m.en_bolsa ? `${m.en_bolsa} en la bolsa` : null), kpi('Realizadas', m.completadas), kpi('Canceladas', m.canceladas),
      kpi('Respuesta media', minTxt(m.respuesta_min), 'hasta que se acepta'), kpi('Resolución media', minTxt(m.resolucion_min), 'hasta que se cierra')),
    el('div', { class: 'dos-col' }, el('div', {}, el('h5', {}, 'Cómo terminaron'), barrasH(m.por_resolucion)), el('div', {}, el('h5', {}, 'Qué se pidió'), barrasH(m.por_accion)), el('div', {}, el('h5', {}, 'Tipo de falla'), barrasH(m.por_tipo))));
  vaciar(zona);
  if (e.rol === 'tecnico') return zona.append(el('div', { class: 'barra-filtros' }, el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Período'), selDias)), bloqueMant);
  const k = e.kpis, d = e.dispositivos, auto = k.resueltas ? Math.round((100 * k.automaticas) / k.resueltas) : null;
  zona.append(el('div', { class: 'barra-filtros' }, el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Período'), selDias)),
    el('div', { class: 'kpis' }, kpi('Alertas', k.alertas, `${k.abiertas} sin resolver`, 'alertas-abiertas'), kpi('Resueltas', k.resueltas, auto == null ? null : `${auto} % solas`, 'resueltas'), kpi('Tiempo de respuesta', minTxt(k.respuesta_min), 'hasta reconocerla'),
      kpi('Tiempo de resolución', minTxt(k.resolucion_min)), kpi('Soluciones cuestionadas', k.cuestionadas, 'la medición no las respaldó'), kpi('Cortes de comunicación', k.cortes, null, 'perdio-comunicacion'), kpi('Movimientos de equipos', k.movimientos),
      kpi('Dispositivos en línea', `${d.en_linea} de ${d.total}`, `${d.sin_comunicacion} sin comunicación · ${d.con_gps} con GPS`, 'dispositivos-en-linea')),
    el('section', { class: 'card-informe' }, el('h4', {}, 'Alertas por día'), graficoDias(e.serie)),
    el('div', { class: 'dos-col' }, el('section', { class: 'card-informe' }, el('h4', {}, 'Por tipo'), barrasH(e.por_tipo), el('h5', {}, 'Por severidad'), barrasH(e.por_severidad)),
      el('section', { class: 'card-informe' }, el('h4', {}, 'Cultivos con más alertas'), barrasH(e.por_cultivo)),
      el('section', { class: 'card-informe' }, el('h4', {}, 'Dispositivos con más cortes'), barrasH(e.cortes_por_dispositivo, 'Ningún corte en este período.'))), bloqueMant);
}
