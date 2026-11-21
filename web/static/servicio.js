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
      AQ.usuario.rol !== 'tecnico' ? formularioDerivar({ id: null, dispositivo_id: d.id, tipo: 'otro' }, async () => {}, 'boton-secundario', false) : null, msg);
  };
  tarjetas.append(lista.map(tarjeta));
  const vUbicacionRecargar = () => vUbicacion(zona);
  vaciar(zona).append(el('p', { class: 'ayuda' }, 'Dónde está cada dispositivo según su GPS. Si un equipo se mueve, aquí se ve su última posición y su recorrido.'), caja, trayectoTxt,
    lista.length ? tarjetas : el('p', { class: 'ayuda' }, 'No hay dispositivos para mostrar.'));
}

/*  Solicitudes técnicas: la ve el técnico (lo suyo y la bolsa) y quienes las piden  */
async function vServicioTecnico(zona) {
  const rol = AQ.usuario.rol, esTec = rol === 'tecnico', puedePedir = ['encargado_cultivo', 'supervisor', 'administrador'].includes(rol);
  const filtro = zona.dataset.filtro || 'abiertas', lista = el('div', { class: 'lista-mantenciones' }), nueva = el('div', {});
  const sel = el('select', { onchange: (e) => { zona.dataset.filtro = e.target.value; cargar(true); } }, [['abiertas', 'Abiertas (pendientes y en curso)'], ['', 'Todas'], ['completada', 'Realizadas'], ['cancelada', 'Canceladas']]
    .map(([v, t]) => el('option', { value: v, selected: v === filtro }, t)));
  const cargar = async (forzar) => {
    if (!forzar && (AQ.editando || /^(TEXTAREA|SELECT|INPUT)$/.test((document.activeElement || {}).tagName || ''))) return;
    const datos = await api.get(`/mantenciones${qs({ estado: zona.dataset.filtro || 'abiertas' })}`);
    vaciar(lista).append(datos.length ? datos.map((m) => el('article', { class: 'card-informe' }, cajaMantencion(m, () => cargar(true)))) : el('p', { class: 'ayuda' },
      esTec ? 'No tienes solicitudes. Cuando te asignen una, o llegue una a la bolsa, aparecerá aquí con aviso.' : 'No hay solicitudes con este filtro.'));
  };
  const pedir = async () => {
    const disp = await api.get('/dispositivos'), dsel = el('select', {}, disp.map((d) => el('option', { value: d.id }, `${d.cultivo_nombre || 'Sin cultivo'} · ${d.codigo}`)));
    const zonaForm = el('div', {}); let formulario = null;
    const pintar = () => { formulario = formularioDerivar({ id: null, dispositivo_id: dsel.value, tipo: 'otro' }, async () => { vaciar(nueva); await cargar(true); }, 'boton-secundario', false); vaciar(zonaForm).append(formulario); formulario.querySelector('button').click(); };
    dsel.addEventListener('change', pintar); vaciar(nueva).append(el('div', { class: 'card-informe' }, el('h4', {}, 'Nueva solicitud sin alerta (mantención, retiro, reemplazo)'), el('label', { class: 'campo' }, el('span', {}, 'Dispositivo'), dsel), zonaForm,
      el('button', { type: 'button', class: 'boton-texto', onclick: () => { vaciar(nueva); AQ.editando = false; } }, 'Cerrar')));
    pintar();
  };
  vaciar(zona).append(el('p', { class: 'ayuda' }, esTec ? 'Solicitudes que te asignaron y las que están en la bolsa (sin técnico). La primera que aceptes es tuya; si no puedes, devuélvela con el motivo.'
      : 'Pedidos de arreglo, reemplazo, retiro o mantención de los dispositivos, con su estado y quién los atiende.'),
    el('div', { class: 'barra-filtros' }, el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Mostrar'), sel),
      puedePedir ? el('button', { type: 'button', class: 'boton-secundario', onclick: pedir }, '+ Nueva solicitud sin alerta') : null), nueva, lista);
  await cargar(true);
  return { refrescar: () => cargar(false), cada: 15000 };
}

/*  Estadísticas  */
const kpi = (titulo, valor, nota) => el('div', { class: 'kpi' }, el('span', { class: 'kpi-tit' }, titulo), el('strong', {}, valor == null ? '—' : valor), nota ? el('small', { class: 'suave' }, nota) : null);
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
    el('div', { class: 'kpis' }, kpi('Solicitudes', m.total), kpi('Abiertas ahora', m.abiertas, m.en_bolsa ? `${m.en_bolsa} en la bolsa` : null), kpi('Realizadas', m.completadas), kpi('Canceladas', m.canceladas),
      kpi('Respuesta media', minTxt(m.respuesta_min), 'hasta que se acepta'), kpi('Resolución media', minTxt(m.resolucion_min), 'hasta que se cierra')),
    el('div', { class: 'dos-col' }, el('div', {}, el('h5', {}, 'Cómo terminaron'), barrasH(m.por_resolucion)), el('div', {}, el('h5', {}, 'Qué se pidió'), barrasH(m.por_accion)), el('div', {}, el('h5', {}, 'Tipo de falla'), barrasH(m.por_tipo))));
  vaciar(zona);
  if (e.rol === 'tecnico') return zona.append(el('div', { class: 'barra-filtros' }, el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Período'), selDias)), bloqueMant);
  const k = e.kpis, d = e.dispositivos, auto = k.resueltas ? Math.round((100 * k.automaticas) / k.resueltas) : null;
  zona.append(el('div', { class: 'barra-filtros' }, el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Período'), selDias)),
    el('div', { class: 'kpis' }, kpi('Alertas', k.alertas, `${k.abiertas} sin resolver`), kpi('Resueltas', k.resueltas, auto == null ? null : `${auto} % solas`), kpi('Tiempo de respuesta', minTxt(k.respuesta_min), 'hasta reconocerla'),
      kpi('Tiempo de resolución', minTxt(k.resolucion_min)), kpi('Soluciones cuestionadas', k.cuestionadas, 'la medición no las respaldó'), kpi('Cortes de comunicación', k.cortes), kpi('Movimientos de equipos', k.movimientos),
      kpi('Dispositivos en línea', `${d.en_linea} de ${d.total}`, `${d.sin_comunicacion} sin comunicación · ${d.con_gps} con GPS`)),
    el('section', { class: 'card-informe' }, el('h4', {}, 'Alertas por día'), graficoDias(e.serie)),
    el('div', { class: 'dos-col' }, el('section', { class: 'card-informe' }, el('h4', {}, 'Por tipo'), barrasH(e.por_tipo), el('h5', {}, 'Por severidad'), barrasH(e.por_severidad)),
      el('section', { class: 'card-informe' }, el('h4', {}, 'Cultivos con más alertas'), barrasH(e.por_cultivo)),
      el('section', { class: 'card-informe' }, el('h4', {}, 'Dispositivos con más cortes'), barrasH(e.cortes_por_dispositivo, 'Ningún corte en este período.'))), bloqueMant);
}
