/* Utilidades compartidas por la web (monitoreo), la app móvil y la consola /admin. Sin dependencias, sin Node. */
const AQ = { csrf: null, usuario: null, alSalir: null, editando: false };
const ROL = { administrador: 'Administrador', supervisor: 'Supervisor', encargado_cultivo: 'Encargado de cultivo', tecnico: 'Técnico / servicio técnico' };
const ETIQUETA = { en_linea: 'En línea', sin_comunicacion: 'Sin comunicación', sin_datos: 'Sin datos', activa: 'Activa',
    reconocida: 'Reconocida', revisada: 'Revisada · falta confirmar', resuelta: 'Resuelta', conectado: 'Conectado', diferido: 'Diferido' };
const TIPO_EVENTO = { dispositivo_sin_comunicacion: 'Sin comunicación', dispositivo_reconectado: 'Reconexión',
  sincronizacion_diferida: 'Sincronización diferida', medicion_rechazada: 'Datos rechazados' };

/* append() ignora null/false: evita que aparezca el texto «null» en pantalla cuando un elemento es opcional. */
const _anexar = Element.prototype.append;
Element.prototype.append = function (...hijos) { return _anexar.apply(this, hijos.flat(Infinity).filter((h) => h != null && h !== false)); };

class ErrorApi extends Error { constructor(m, codigo, detalles) { super(m); this.codigo = codigo; this.detalles = detalles || []; } }

/* Transporte
   * Navegador (web o /movil servido por Django): fetch directo a /api con cookie de sesión.
   * App Android: SOLO a través de Firebase (relé, ver core/relay.py). El celular deja la petición en relay/sol/{id}, el servidor
     la escucha en tiempo real, la ejecuta y deja la respuesta en relay/resp/{id}. No hace falta estar en la misma WiFi:
     basta con que el celular y el servidor tengan internet. */
const _local = { leer: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  guardar: (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* sin almacenamiento */ } } };
const hexAleatorio = (n) => { const a = new Uint8Array(n); crypto.getRandomValues(a); return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join(''); };
const RELE = (() => {
  try { if (window.AquaAndroid && AquaAndroid.firebaseUrl) { const u = AquaAndroid.firebaseUrl(); if (u) return u.replace(/\/+$/, ''); } } catch { /* no es la app */ }
  
  const m = location.hostname.match(/^([a-z0-9-]+)\.(web\.app|firebaseapp\.com)$/);
  if (m) return `https://${m[1]}-default-rtdb.firebaseio.com`;
  // Pruebas desde un navegador: /movil?rele=https://aquaplants-370b7-default-rtdb.firebaseio.com/. Solo se aceptan bases de Firebase
  // (o la base local): un enlace con otra dirección podría desviar la contraseña a un sitio ajeno.
  const q = new URLSearchParams(location.search).get('rele');
  const ok = q && /^(https:\/\/[a-z0-9-]+\.(firebaseio\.com|[a-z0-9-]+\.firebasedatabase\.app)|http:\/\/(127\.0\.0\.1|localhost):\d+)\/?$/.test(q);
  return ok ? q.replace(/\/+$/, '') : null;
})();
const Rele = { token: _local.leer('aq_token'), equipo: _local.leer('aq_equipo_rele') };
if (RELE && !Rele.equipo) { Rele.equipo = hexAleatorio(8); _local.guardar('aq_equipo_rele', Rele.equipo); }

function esperarRespuesta(id, limiteMs) {
  const url = `${RELE}/relay/resp/${id}.json`;
  return new Promise((resolve, reject) => {
    let listo = false, es = null, sondeo = null, respaldo = null;
    const fin = (v, err) => {
      if (listo) return; listo = true; clearTimeout(tope); clearTimeout(respaldo); clearInterval(sondeo); if (es) es.close();
      if (err) reject(err); else resolve(v);
    };
    const tope = setTimeout(() => fin(null, new ErrorApi('El servidor AquaPlants no respondió a través de Firebase. Revisa que el computador tenga '
      + 'internet y esté ejecutando «python manage.py runserver».', 'servidor_no_responde')), limiteMs);
    const revisar = async () => { try { const x = await (await fetch(url, { cache: 'no-store' })).json(); if (x && x.s) fin(x); } catch { /* se reintenta */ } };
    const sondear = () => { if (!sondeo && !listo) { sondeo = setInterval(revisar, 400); revisar(); } };
    if (window.EventSource && RELE.startsWith('https://')) {   // tiempo real (Server-Sent Events de Firebase); si falla, se consulta cada 0,4 s
      try {
        es = new EventSource(url);
        es.addEventListener('put', (e) => { try { const d = JSON.parse(e.data); if (d.path === '/' && d.data && d.data.s) fin(d.data); } catch { /* evento parcial */ } });
        es.onerror = () => { if (es) es.close(); es = null; sondear(); };
      } catch { sondear(); }
      respaldo = setTimeout(sondear, 2500);
    } else sondear();
  });
}

async function pedirRele(metodo, ruta, cuerpo) {
  const id = `${String(Date.now()).padStart(13, '0')}_${hexAleatorio(16)}`;
  const sol = { m: metodo, r: '/api' + ruta, ts: { '.sv': 'timestamp' }, d: Rele.equipo, ua: navigator.userAgent.slice(0, 200) };
  if (cuerpo !== undefined) sol.b = JSON.stringify(cuerpo);
  if (Rele.token) sol.t = Rele.token;
  if (metodo !== 'GET' && AQ.csrf) sol.c = AQ.csrf;
  let w;
  try { w = await fetch(`${RELE}/relay/sol/${id}.json`, { method: 'PUT', body: JSON.stringify(sol) }); }
  catch { throw new ErrorApi('Sin conexión a internet: el celular no pudo llegar a Firebase.', 'sin_internet'); }
  if (!w.ok) throw new ErrorApi(`Firebase rechazó la petición (HTTP ${w.status}). Las reglas de la base deben incluir «relay» `
    + '(se publican solas al iniciar el servidor; ver docs/conexion-movil.md).', 'firebase_reglas');
  const r = await esperarRespuesta(id, 30000);
  fetch(`${RELE}/relay/resp/${id}.json`, { method: 'DELETE' }).catch(() => {});
  if ('k' in r) { Rele.token = r.k || null; _local.guardar('aq_token', Rele.token); }
  return r;
}

async function pedir(metodo, ruta, cuerpo) {
  let estado, datos, archivo = null;
  if (RELE) {
    const r = await pedirRele(metodo, ruta, cuerpo);
    estado = r.s;
    if (r.b64) archivo = { nombre: r.fn, tipo: r.ct, base64: r.b64 };
    else { try { datos = r.b ? JSON.parse(r.b) : null; } catch { datos = null; } }
    if (estado === 401 && ruta !== '/auth/login') { Rele.token = null; _local.guardar('aq_token', null); }
  } else {
    const cab = { Accept: 'application/json' };
    if (cuerpo !== undefined) cab['Content-Type'] = 'application/json';
    if (metodo !== 'GET' && AQ.csrf) cab['X-CSRF-Token'] = AQ.csrf;
    const r = await fetch('/api' + ruta, { method: metodo, headers: cab, credentials: 'same-origin', body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined });
    estado = r.status;
    datos = r.status === 204 ? null : await r.json().catch(() => null);
  }
  if (estado === 401 && ruta !== '/auth/login' && AQ.alSalir && !AQ.enLogin) AQ.alSalir();
  if (estado < 200 || estado >= 300) { const e = (datos && datos.error) || {}; throw new ErrorApi(e.mensaje || `Error ${estado}`, e.codigo, e.detalles); }
  return archivo || datos;
}
const api = { get: (r) => pedir('GET', r), post: (r, c) => pedir('POST', r, c === undefined ? {} : c), patch: (r, c) => pedir('PATCH', r, c),
  put: (r, c) => pedir('PUT', r, c), del: (r) => pedir('DELETE', r) };

/* Descarga un archivo de la API (informes). En la app se guarda en «Descargas» del celular. */
async function descargar(ruta) {
  if (!RELE) { location.href = '/api' + ruta; return 'Descarga iniciada.'; }
  const a = await api.get(ruta);
  if (!a || !a.base64) throw new ErrorApi('La respuesta no trae un archivo.');
  if (window.AquaAndroid && AquaAndroid.guardarArchivo) return AquaAndroid.guardarArchivo(a.nombre, a.tipo, a.base64);
  const enlace = el('a', { href: `data:${a.tipo};base64,${a.base64}`, download: a.nombre }); document.body.append(enlace); enlace.click(); enlace.remove();
  return `Archivo ${a.nombre} descargado.`;
}

/* Aviso flotante (no lo borra el refresco automático de la pantalla). tipo: 'ok' | 'error' | '' */
let _toast = null;
function mostrarAviso(texto, tipo = '') {
  if (_toast) _toast.remove();
  _toast = el('div', { class: `toast ${tipo === 'error' ? 'critica' : tipo}`, role: tipo === 'error' ? 'alert' : 'status', onclick: () => { if (_toast) _toast.remove(); } }, texto);
  document.body.append(_toast);
  const este = _toast;
  setTimeout(() => { if (este === _toast && _toast) { _toast.remove(); _toast = null; } }, tipo === 'error' ? 9000 : 5000);
}
const qs = (o) => { const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`); return p.length ? `?${p.join('&')}` : ''; };
const mensajeError = (e) => [e.message, ...(e.detalles || []).map((d) => `${d.campo}: ${d.mensaje}`)].join(' ');

/* Constructor de DOM seguro: todo el texto va como textContent (nunca innerHTML). */
function el(tag, attrs, ...hijos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k === 'value') e.value = v;
    else if (k === 'checked') e.checked = !!v;
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);         // la política de seguridad no admite style="..." en texto
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const h of hijos.flat(Infinity)) if (h != null && h !== false) e.append(h.nodeType ? h : document.createTextNode(String(h)));
  return e;
}
const vaciar = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const fmt = (v, dec = 2) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString('es-CL', { maximumFractionDigits: dec }));
const fmtLectura = (v) => fmt(v, 3);
/*  Flujo de una alerta: activa -> reconocida -> revisada -> resuelta 
   Si el valor vuelve a la normalidad antes de que alguien la atienda, el sistema la resuelve sola (queda en el historial). */
const ETIQUETA_ALERTA = { activa: 'Alerta activa', reconocida: 'En revisión', revisada: 'Falta confirmar', ok: 'Sin alertas' };
const ETIQUETA_FALLA = { activa: 'sin atender', reconocida: 'en revisión', revisada: 'solucionada · falta confirmar' };

/* Cada acción de la bitácora se muestra como un paso con su color, su título, quién y cuándo. */
function tipoPaso(accion) {
  if (/autom/.test(accion)) return { clase: 'auto', icono: '↻', titulo: 'Se resolvió sola' };
  if (/no confirmada/.test(accion)) return { clase: 'mal', icono: '⚠', titulo: 'Solución no confirmada por la medición' };
  if (/esperando la medici/.test(accion)) return { clase: 'espera', icono: '⏱', titulo: 'Esperando la medición' };
  if (/deriv/.test(accion)) return { clase: 'tec', icono: '🔧', titulo: 'Derivada a técnico / mantención' };
  if (/mantenci/.test(accion)) return { clase: 'tec', icono: '🔧', titulo: /realizada/.test(accion) ? 'Mantención realizada' : /cancel/.test(accion) ? 'Mantención cancelada' : /devolvi/.test(accion) ? 'Mantención devuelta a la bolsa'
    : /asign/.test(accion) ? 'Mantención asignada' : /acept/.test(accion) ? 'Mantención aceptada por el técnico' : 'Mantención en curso' };
  if (/no pudo/.test(accion)) return { clase: 'mal', icono: '✖', titulo: 'Revisada: no se pudo solucionar' };
  if (/rechaz/.test(accion)) return { clase: 'mal', icono: '↩', titulo: 'Solución rechazada' };
  if (/resolvi/.test(accion)) return { clase: 'bien', icono: '✔', titulo: 'Resuelta' };
  if (/confirm/.test(accion)) return { clase: 'bien', icono: '✔', titulo: 'Solución confirmada' };
  if (/soluci/.test(accion)) return { clase: 'rev', icono: '🛠', titulo: 'Revisada: quedó solucionado' };
  if (/reconoc/.test(accion)) return { clase: 'curso', icono: '👀', titulo: 'En revisión' };
  return { clase: '', icono: '•', titulo: accion };
}
function pasosDeAlerta(a) {
  const pasos = [], quien = (n, rol) => (n ? `${n}${rol && ROL[rol] ? ' · ' + ROL[rol] : ''}` : '');
  const sin = a.tipo === 'sin_comunicacion';
  pasos.push({ clase: 'detecta', icono: sin ? '📡' : '⚠', titulo: sin ? 'Se perdió la comunicación' : 'Alerta detectada', cuando: a.creada_en, quien: 'Sistema',
    texto: sin ? 'El dispositivo dejó de enviar datos.'
      : `${a.valor_detectado != null ? `Valor detectado: ${fmt(a.valor_detectado, 2)} ${a.unidad || ''}. ` : ''}${a.regla ? `Regla: ${a.regla.operador} ${fmt(a.regla.umbral, 2)} ${a.unidad || ''}.` : ''}`.trim() });
  if (a.bitacora && a.bitacora.length) {
    a.bitacora.forEach((b) => { const t = tipoPaso(b.accion); pasos.push({ ...t, cuando: b.en, quien: quien(b.por, b.rol), texto: b.nota }); });
  } else {                                                      // alertas antiguas, anteriores a la bitácora
    if (a.reconocida_por) pasos.push({ ...tipoPaso('reconoció'), cuando: a.reconocida_en, quien: a.reconocida_por, texto: 'Fue a revisar.' });
    if (a.revision_por) pasos.push({ ...tipoPaso(a.revision_solucionado ? 'dio solución' : 'no pudo solucionar'), cuando: a.revision_en, quien: a.revision_por, texto: a.revision_nota });
    if (a.verificacion_por) pasos.push({ ...tipoPaso(a.verificacion_aprobada ? 'confirmó' : 'rechazó'), cuando: a.verificacion_en, quien: a.verificacion_por, texto: a.verificacion_nota });
    if (a.estado === 'resuelta' && a.resuelta_auto) pasos.push({ ...tipoPaso('automáticamente'), cuando: a.resuelta_en, quien: 'Sistema', texto: 'El valor volvió a la normalidad.' });
  }
  if (a.estado !== 'resuelta') {
    const rechazada = a.verificacion_aprobada === false && a.estado === 'reconocida';
    const mant = a.mantencion && ['pendiente', 'en_curso'].includes(a.mantencion.estado) ? a.mantencion : null;
    pasos.push({ clase: 'pend', icono: '⏳', titulo: 'Pendiente',
      texto: mant ? `Derivada a mantención (${mant.tipo_texto}, ${mant.estado_texto.toLowerCase()}). Se resolverá sola cuando el dato vuelva a la normalidad.`
        : a.solucion_cuestionada && a.estado === 'reconocida' ? 'La medición no respaldó la solución informada: hay que volver a revisar o derivar a un técnico.'
        : a.estado === 'revisada' && a.espera_medicion_hasta ? `Se informó la solución. La medición tiene hasta las ${horaCorta(a.espera_medicion_hasta)} para confirmarlo; si se normaliza, la alerta se resuelve sola.`
        : a.estado === 'activa' ? 'Nadie ha ido a revisar todavía. Si el valor vuelve a la normalidad antes, se resolverá sola.'
        : a.estado === 'revisada' ? 'Falta que el supervisor o el administrador confirme la solución.'
          : rechazada ? 'Se rechazó la solución: hay que volver a revisar.' : `${a.reconocida_por || 'Alguien'} está revisando.` });
  } else if (a.resuelta_en) {
    const min = Math.max(0, Math.round((Date.parse(a.resuelta_en) - Date.parse(a.creada_en)) / 60000));
    pasos.push({ clase: 'fin', icono: '■', titulo: 'Cerrada', texto: `Duró ${min < 1 ? 'menos de 1 min' : min < 60 ? min + ' min' : `${Math.floor(min / 60)} h ${min % 60} min`}.` });
  }
  return pasos;
}
/* Las cajas de cada paso: un fondo apenas distinto, una franja de color y una separación mínima entre una y otra. */
function pasosAlerta(a) {
  return el('ol', { class: 'pasos' }, pasosDeAlerta(a).map((p) => el('li', { class: `paso ${p.clase}` },
    el('div', { class: 'paso-cab' }, el('span', { class: 'paso-ico', 'aria-hidden': 'true' }, p.icono), el('strong', { class: 'paso-tit' }, p.titulo),
      p.cuando ? el('time', { class: 'paso-hora' }, fechaHora(p.cuando)) : null),
    p.quien ? el('div', { class: 'paso-quien' }, p.quien) : null,
    p.texto ? el('div', { class: 'paso-texto' }, p.texto) : null)));
}
const trazaAlerta = (a) => [pasosAlerta(a)];
function accionesAlerta(a, alTerminar, clase = 'boton-secundario') {
  const rol = AQ.usuario.rol, caja = el('div', { class: 'acciones-alerta' }), msg = el('div', {});
  const esEncargado = ['encargado_cultivo', 'supervisor', 'administrador'].includes(rol), esSupervisor = ['supervisor', 'administrador'].includes(rol);
  const enviar = async (ruta, cuerpo, ok) => {
    caja.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try { await api.post(`/alertas/${a.id}/${ruta}`, cuerpo); AQ.editando = false; mostrarAviso(ok, 'ok'); await alTerminar(ok); }
    catch (e) { vaciar(msg).append(aviso('error', mensajeError(e))); mostrarAviso(mensajeError(e), 'error'); }        // el aviso flotante no se pierde con el refresco
    finally { caja.querySelectorAll('button').forEach((b) => { b.disabled = false; }); }
  };
  const nota = (ph) => el('textarea', { rows: 2, maxlength: 500, placeholder: ph, oninput: (e) => { AQ.editando = e.target.value.length > 0; } });
  const boton = (t, f) => el('button', { type: 'button', class: clase, onclick: f }, t);
  caja.append(...avisosAlerta(a, alTerminar, clase));
  if (a.estado === 'activa') {
    caja.append(esEncargado ? boton('Reconocer (voy a revisar)', () => enviar('reconocer', {}, 'Alerta reconocida')) : el('span', { class: 'meta suave' }, 'Nadie ha ido a revisar todavía.'));
  } else if (a.estado === 'reconocida') {
    if (esEncargado) {
      const t = nota('¿Qué encontraste y qué hiciste? (queda en la bitácora)');
      const revisar = (si) => {
        if (t.value.trim().length < 5) { mostrarAviso('Escribe qué encontraste y qué hiciste (al menos 5 letras): queda en la bitácora.', 'error'); t.focus(); return; }
        enviar('revisar', { solucionado: si, nota: t.value.trim() }, si ? (rol === 'encargado_cultivo' ? 'Solución registrada: falta la confirmación' : 'Problema solucionado: alerta resuelta')
          : 'Registrado: no se pudo solucionar (la alerta sigue abierta)');
      };
      caja.append(t, el('div', { class: 'botones-revision' },
        el('button', { type: 'button', class: `${clase} boton-ok`, onclick: () => revisar(true) }, '✔ Sí, quedó solucionado'),
        el('button', { type: 'button', class: `${clase} boton-mal`, onclick: () => revisar(false) }, '✖ No pude solucionarlo')));
      if (rol !== 'encargado_cultivo') caja.append(el('div', { class: 'meta suave' }, `Como ${ROL[rol].toLowerCase()} tu revisión es la definitiva: si queda solucionado, la alerta se resuelve de inmediato y queda en la bitácora a tu nombre; si no, sigue abierta.`));
    } else caja.append(el('span', { class: 'meta suave' }, 'En revisión por el encargado.'));
  } else if (a.estado === 'revisada') {
    if (esSupervisor) {
      const t = nota('Comentario (obligatorio si la rechazas)');
      caja.append(el('div', { class: 'meta' }, `${a.revision_por || 'Alguien'} informó que quedó solucionado${a.revision_nota ? ': «' + a.revision_nota + '»' : ''}.`), t,
        el('div', { class: 'botones-revision' },
          el('button', { type: 'button', class: `${clase} boton-ok`, onclick: () => enviar('verificar', { aprobada: true, nota: t.value }, 'Solución confirmada: alerta resuelta') }, '✔ Confirmar solución'),
          el('button', { type: 'button', class: `${clase} boton-mal`, onclick: () => enviar('verificar', { aprobada: false, nota: t.value }, 'Solución rechazada: vuelve a revisión') }, '✖ Rechazar')));
    } else caja.append(el('span', { class: 'meta suave' }, 'Esperando la confirmación del supervisor.'));
  }
  const mantAbierta = a.mantencion && ['pendiente', 'en_curso'].includes(a.mantencion.estado);
  if (esEncargado && !mantAbierta) caja.append(formularioDerivar(a, alTerminar, clase, a.requiere_tecnico));   // en cualquier alerta abierta, también recién creada
  return caja.append(msg), caja;
}

/*  Solución cuestionada y derivación a técnico / mantención  */
const TIPOS_MANTENCION = [['tecnico', 'Visita de técnico'], ['bateria', 'Cambio de batería'], ['dispositivo', 'Cambio de dispositivo'], ['configuracion', 'Falla de configuración'],
  ['componente', 'Falla de un componente'], ['sensor', 'Revisión o cambio de sensor'], ['red', 'Revisión de red, WiFi o energía'], ['otro', 'Otra mantención']];
const ACCIONES_MANTENCION = [['arreglo', 'Arreglo o reparación'], ['reemplazo', 'Reemplazo del equipo'], ['retiro', 'Retiro del equipo'], ['mantencion', 'Mantención preventiva']];
const RESOLUCIONES = [['arreglado', 'Arreglado'], ['reemplazado', 'Reemplazado'], ['retirado', 'Retirado'], ['mantencion_realizada', 'Mantención realizada'], ['sin_solucion', 'No se pudo resolver']];
function avisosAlerta(a, alTerminar, clase) {
  const salida = [];
  if (a.solucion_cuestionada && a.estado !== 'resuelta') salida.push(el('div', { class: 'banner-alerta mal' }, el('strong', {}, '⚠ La medición no respaldó una solución informada. '), 'Queda registrado en la bitácora y en la auditoría.'));
  if (a.requiere_tecnico) salida.push(el('div', { class: 'banner-alerta tec' }, el('strong', {}, '🔧 Se recomienda derivar a un técnico. '), 'Revisa la conexión, la batería o pide el cambio o retiro del dispositivo si no vuelve a comunicarse.'));
  if (a.mantencion) salida.push(cajaMantencion(a.mantencion, alTerminar, clase));
  return salida;
}
const enlaceMapa = (u) => `https://www.google.com/maps?q=${u.lat},${u.lng}`;
const enlaceRuta = (u) => `https://www.google.com/maps/dir/?api=1&destination=${u.lat},${u.lng}`;
/* Una solicitud técnica con todo lo que hace falta para actuar. Los controles dependen del rol:
   técnico = aceptar, cerrar con el resultado o devolver a la bolsa; supervisor y administrador = además asignar, poner en curso o cancelar. */
function cajaMantencion(m, alTerminar, clase = 'boton-secundario') {
  const abierta = ['pendiente', 'en_curso'].includes(m.estado), rol = AQ.usuario.rol, esTec = rol === 'tecnico', jefe = ['supervisor', 'administrador'].includes(rol);
  const enlaces = m.ubicacion ? el('div', { class: 'enlaces-mapa' }, el('a', { href: enlaceMapa(m.ubicacion), target: '_blank', rel: 'noopener' }, '📍 Ver en Google Maps'), ' · ',
    el('a', { href: enlaceRuta(m.ubicacion), target: '_blank', rel: 'noopener' }, 'Cómo llegar'), el('span', { class: 'suave' }, ` · posición ${m.ubicacion.origen === 'manual' ? 'indicada a mano' : 'del GPS'}, ${hace(m.ubicacion.actualizada_en)}`))
    : (m.dispositivo ? el('div', { class: 'meta suave' }, 'Este dispositivo todavía no ha informado su posición.') : null);
  const caja = el('div', { class: `mantencion ${m.estado}` },
    el('div', { class: 'mantencion-cab' }, el('strong', {}, `🔧 ${m.tipo_texto}`), el('span', {}, el('span', { class: 'etiqueta m-accion' }, m.accion_texto || ''), ' ', el('span', { class: `etiqueta m-${m.estado}` }, m.estado_texto))),
    m.dispositivo ? el('div', { class: 'meta' }, [m.instalacion, m.cultivo, m.dispositivo ? `Dispositivo ${m.dispositivo}` : null].filter(Boolean).join(' · '), m.direccion ? el('div', { class: 'suave' }, `Dirección: ${m.direccion}`) : null) : null,
    enlaces,
    el('div', { class: 'meta suave' }, `Pedida por ${m.creada_por || '—'}${m.solicitante && m.solicitante.email ? ` (${m.solicitante.email})` : ''} · ${fechaHora(m.creada_en)}`
      + `${m.tecnico_nombre ? ` · Técnico: ${m.tecnico_nombre}` : m.en_bolsa ? ' · En la bolsa: la puede tomar cualquier técnico' : ''}${m.tecnico ? ` · ${m.tecnico}` : ''}`),
    m.alerta ? el('div', { class: 'meta' }, el('strong', {}, 'Alerta: '), m.alerta) : null,
    m.nota ? el('div', { class: 'paso-texto sin-sangria' }, m.nota) : null,
    m.resultado ? el('div', { class: 'paso-texto sin-sangria' }, el('strong', {}, `${m.resolucion_texto || 'Resultado'}: `), m.resultado, m.repuestos ? ` · Repuestos: ${m.repuestos}` : '',
      m.completada_por ? ` (${m.completada_por}, ${fechaHora(m.completada_en)})` : '') : null);
  if (!abierta || !(esTec || jefe)) return caja;
  const msg = el('div', {});
  const mover = async (cuerpo, ok) => { try { await api.patch(`/mantenciones/${m.id}`, cuerpo); AQ.editando = false; mostrarAviso(ok, 'ok'); await alTerminar(ok); } catch (e) { vaciar(msg).append(aviso('error', mensajeError(e))); } };
  const nota = el('textarea', { rows: 2, maxlength: 500, placeholder: 'Qué se hizo (o por qué se devuelve o se cancela)', oninput: (e) => { AQ.editando = e.target.value.length > 0; } });
  const res = el('select', {}, el('option', { value: '' }, 'Cómo quedó…'), RESOLUCIONES.map(([v, t]) => el('option', { value: v }, t)));
  const rep = el('input', { type: 'text', maxlength: 300, placeholder: 'Repuestos o materiales usados (opcional)' });
  const tec = el('input', { type: 'text', maxlength: 120, placeholder: 'Técnico o empresa (texto, opcional)', value: m.tecnico || '' });
  const boton = (t, f, extra = '') => el('button', { type: 'button', class: `${clase} ${extra}`.trim(), onclick: f }, t);
  const cerrar = () => { if (!res.value) { vaciar(msg).append(aviso('error', 'Indica cómo quedó (arreglado, reemplazado, retirado…).')); return; }
    mover({ estado: 'completada', nota: nota.value.trim(), resolucion: res.value, repuestos: rep.value.trim(), tecnico: tec.value.trim() }, 'Solicitud cerrada'); };
  const acciones = [];
  if (m.estado === 'pendiente') acciones.push(boton(esTec ? '✋ Aceptar la solicitud' : 'Poner en curso', () => mover({ estado: 'en_curso' }, esTec ? 'Solicitud aceptada' : 'Mantención en curso'), 'boton-ok'));
  caja.append(el('div', { class: 'cerrar-mant' }, el('strong', {}, 'Registrar el resultado'), res, rep, esTec ? null : tec, nota,
    el('div', { class: 'botones-revision' }, ...acciones, boton('✔ Cerrar con este resultado', cerrar, 'boton-ok'),
      esTec ? boton('↩ Devolver a la bolsa', () => mover({ estado: 'devuelta', nota: nota.value.trim() }, 'Devuelta a la bolsa de técnicos')) : null,
      jefe ? boton('Cancelar la solicitud', () => mover({ estado: 'cancelada', nota: nota.value.trim() }, 'Solicitud cancelada'), 'boton-mal') : null)));
  if (jefe) {                                                   // asignar directamente a un técnico (conexión directa)
    const sel = el('select', {}, el('option', { value: '' }, 'Elegir técnico…'));
    api.get('/tecnicos').then((l) => l.forEach((t) => sel.append(el('option', { value: t.id, selected: t.id === m.tecnico_id }, t.nombre)))).catch(() => {});
    caja.append(el('div', { class: 'asignar' }, el('strong', {}, 'Asignar a'), sel, boton('Asignar', () => { if (!sel.value) { vaciar(msg).append(aviso('error', 'Elige un técnico.')); return; } mover({ tecnico_id: sel.value }, 'Solicitud asignada'); })));
  }
  return caja.append(msg), caja;
}
/* Derivar una alerta (o pedir directamente un arreglo, retiro, reemplazo o mantención) al técnico o servicio técnico. */
function formularioDerivar(a, alTerminar, clase, destacado) {
  const zona = el('div', { class: 'derivar' });
  const abrir = () => {
    const tipo = el('select', {}, TIPOS_MANTENCION.map(([v, t]) => el('option', { value: v, selected: v === (a.tipo === 'sin_comunicacion' ? 'tecnico' : 'otro') }, t)));
    const accion = el('select', {}, ACCIONES_MANTENCION.map(([v, t]) => el('option', { value: v }, t)));
    const quien = el('select', {}, el('option', { value: '' }, 'Automático: el técnico de la instalación o cualquiera disponible'));
    api.get('/tecnicos').then((l) => l.forEach((t) => quien.append(el('option', { value: t.id }, t.nombre)))).catch(() => {});
    const tec = el('input', { type: 'text', maxlength: 120, placeholder: 'Técnico o empresa en texto (opcional)' });
    const nota = el('textarea', { rows: 2, maxlength: 500, placeholder: '¿Qué pasa y qué se probó? Esto lo lee el técnico.', oninput: (e) => { AQ.editando = e.target.value.length > 0; } });
    const enviar = async () => {
      if (nota.value.trim().length < 5) { mostrarAviso('Cuenta qué pasa (al menos 5 letras).', 'error'); nota.focus(); return; }
      const cuerpo = { tipo: tipo.value, accion: accion.value, nota: nota.value.trim(), ...(tec.value.trim() ? { tecnico: tec.value.trim() } : {}), ...(quien.value ? { tecnico_id: quien.value } : {}) };
      try { await api.post(a.id ? `/alertas/${a.id}/derivar` : '/mantenciones', a.id ? cuerpo : { ...cuerpo, dispositivo_id: a.dispositivo_id }); AQ.editando = false; mostrarAviso('Solicitud enviada al servicio técnico', 'ok'); await alTerminar('Derivada'); }
      catch (e) { mostrarAviso(mensajeError(e), 'error'); }
    };
    vaciar(zona).append(el('div', { class: 'derivar-form' }, el('strong', {}, 'Pedir apoyo técnico'), el('label', { class: 'campo' }, el('span', {}, 'Qué falla'), tipo),
      el('label', { class: 'campo' }, el('span', {}, 'Qué se pide'), accion), el('label', { class: 'campo' }, el('span', {}, 'Para'), quien), tec, nota,
      el('div', { class: 'botones-revision' }, el('button', { type: 'button', class: `${clase} boton-ok`, onclick: enviar }, 'Enviar solicitud'),
        el('button', { type: 'button', class: clase, onclick: () => { AQ.editando = false; vaciar(zona).append(boton()); } }, 'Cancelar'))));
    AQ.editando = true; nota.focus();                          // listo para escribir; mientras el formulario está abierto, la pantalla no se refresca sola
  };
  const boton = () => el('button', { type: 'button', class: `${clase} ${destacado ? 'boton-tec' : ''}`, onclick: abrir }, '🔧 Pedir apoyo técnico (arreglo, reemplazo, retiro)');
  return zona.append(boton()), zona;
}

const fechaHora = (iso) => (iso ? new Date(iso).toLocaleString('es-CL') : '—');
const horaCorta = (iso) => (iso ? new Date(iso).toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—');
function hace(iso) {
  if (!iso) return 'sin datos';
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return fechaHora(iso);
}
const estado = (valor) => el('span', { class: `etiqueta ${valor}` }, ETIQUETA[valor] || valor);
const aviso = (tipo, texto) => (texto ? el('p', { class: `aviso aviso-${tipo}`, role: tipo === 'error' ? 'alert' : 'status' }, texto) : null);

/* Tabla: columnas = [{titulo, campo | ver(fila), num}] */
function tabla(columnas, filas, vacio = 'Sin datos.') {
  const cuerpo = (filas || []).map((f) => el('tr', {}, columnas.map((c) => el('td', { class: c.num ? 'num' : '' }, c.ver ? c.ver(f) : (f[c.campo] ?? '—')))));
  return el('div', { class: 'tabla' }, el('table', {}, el('thead', {}, el('tr', {}, columnas.map((c) => el('th', { class: c.num ? 'num' : '' }, c.titulo)))),
    el('tbody', {}, cuerpo.length ? cuerpo : el('tr', {}, el('td', { colspan: columnas.length, class: 'vacio' }, vacio)))));
}

/* Gráfico de línea en SVG puro. puntos = [{t (ms), v}], referencias = [{nombre, valor}] (umbrales de las reglas). */
function grafico(puntos, { unidad = '', alto = 220, referencias = [], compacto = false, marcas = [] } = {}) {
  const NS = 'http://www.w3.org/2000/svg', W = 700, H = alto, m = compacto ? { i: 4, d: 4, a: 6, b: 6 } : { i: 52, d: 12, a: 12, b: 24 };
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('class', 'grafico'); svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `Gráfico de ${unidad || 'valores'}`);
  const nodo = (t, a, txt) => { const n = document.createElementNS(NS, t); for (const k in a) n.setAttribute(k, a[k]); if (txt != null) n.textContent = txt; svg.append(n); return n; };
  if (!puntos || puntos.length < 2) { nodo('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'g-texto' }, 'Sin datos suficientes en este rango'); return svg; }
  const vs = puntos.map((p) => p.v), ts = puntos.map((p) => p.t);
  let mn = Math.min(...vs), mx = Math.max(...vs);
  const span = Math.max(mx - mn, Math.abs((mx + mn) / 2) * 0.25, 0.1);
  const refs = referencias.filter((r) => r.valor >= mn - span && r.valor <= mx + span);
  for (const r of refs) { mn = Math.min(mn, r.valor); mx = Math.max(mx, r.valor); }
  const pad = (mx - mn) * 0.12 || 1; mn -= pad; mx += pad;
  const t0 = ts[0], t1 = ts[ts.length - 1];
  const X = (t) => m.i + ((t - t0) / (t1 - t0 || 1)) * (W - m.i - m.d);
  const Y = (v) => H - m.b - ((v - mn) / (mx - mn)) * (H - m.a - m.b);
  if (!compacto) {
    for (let i = 0; i <= 3; i++) { const v = mn + ((mx - mn) * i) / 3, y = Y(v); nodo('line', { x1: m.i, x2: W - m.d, y1: y, y2: y, class: 'g-rejilla' }); nodo('text', { x: m.i - 6, y: y + 4, 'text-anchor': 'end', class: 'g-texto' }, fmt(v, 2)); }
    for (const f of [0, 0.5, 1]) nodo('text', { x: m.i + f * (W - m.i - m.d), y: H - 6, 'text-anchor': f === 0 ? 'start' : f === 1 ? 'end' : 'middle', class: 'g-texto' },
      new Date(t0 + f * (t1 - t0)).toLocaleString('es-CL', { hour: '2-digit', minute: '2-digit', day: (t1 - t0) > 86400000 ? '2-digit' : undefined, month: (t1 - t0) > 86400000 ? '2-digit' : undefined }));
  }
  for (const r of refs) { const y = Y(r.valor); nodo('line', { x1: m.i, x2: W - m.d, y1: y, y2: y, class: 'g-umbral' }); if (!compacto) nodo('text', { x: W - m.d - 2, y: y - 3, 'text-anchor': 'end', class: 'g-umbral-t' }, r.nombre); }
  for (const k of marcas) {                                  // líneas verticales: cuándo saltó la alerta y cuándo se resolvió
    if (k.t < t0 || k.t > t1) continue;
    const x = X(k.t); nodo('line', { x1: x, x2: x, y1: m.a, y2: H - m.b, class: `g-marca ${k.clase || ''}` });
    if (!compacto) nodo('text', { x: x + (x > W * 0.7 ? -4 : 4), y: m.a + 10, 'text-anchor': x > W * 0.7 ? 'end' : 'start', class: `g-marca-t ${k.clase || ''}` }, k.nombre);
  }
  const d = puntos.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
  nodo('path', { d: `${d} L${X(t1).toFixed(1)},${H - m.b} L${X(t0).toFixed(1)},${H - m.b} Z`, class: 'g-area' });
  nodo('path', { d, class: 'g-linea' });
  const u = puntos[puntos.length - 1]; nodo('circle', { cx: X(u.t), cy: Y(u.v), r: 4, class: 'g-punto' });
  return svg;
}
/* Gráfico que se pide al servidor y se dibuja solo. La pantalla se refresca cada pocos segundos, así que se guarda el último dibujo:
   se vuelve a pedir solo cuando pasó `ttl` ms (null = no cambia más, p. ej. una alerta ya resuelta). */
const _cacheGraf = new Map(), _pidiendoGraf = new Set();
function graficoCargado(clave, ttl, dibujar) {
  const caja = el('div', { class: 'graf-caja' }), hit = _cacheGraf.get(clave);
  caja.append(hit ? hit.nodo.cloneNode(true) : el('div', { class: 'graf-vacio' }, 'Cargando gráfico…'));
  if ((!hit || (ttl != null && Date.now() - hit.en >= ttl)) && !_pidiendoGraf.has(clave)) {
    _pidiendoGraf.add(clave);
    dibujar().then((nodo) => {
      _cacheGraf.set(clave, { en: Date.now(), nodo });
      if (_cacheGraf.size > 300) _cacheGraf.delete(_cacheGraf.keys().next().value);
      vaciar(caja).append(nodo.cloneNode(true));
    }).catch(() => { if (!hit) vaciar(caja).append(el('div', { class: 'graf-vacio' }, 'No se pudo cargar el gráfico.')); })
      .finally(() => _pidiendoGraf.delete(clave));
  }
  return caja;
}
/* Lo que midió el sensor desde un poco antes de la alerta hasta que se resolvió (o hasta ahora), con el límite de la regla. */
function graficoAlerta(a) {
  if (!a.sensor_id) return el('div', { class: 'graf-vacio' }, a.tipo === 'sin_comunicacion' ? 'Sin comunicación: el dispositivo no envió lecturas durante el corte.' : 'Esta alerta no tiene un sensor asociado.');
  const abierta = a.estado !== 'resuelta';
  return graficoCargado(`alerta:${a.id}:${a.estado}`, abierta ? 30000 : null, async () => {
    const t0 = Date.parse(a.creada_en), t1 = a.resuelta_en ? Date.parse(a.resuelta_en) : Date.now(), dur = Math.max(t1 - t0, 60000);
    const desde = Math.max(t0 - Math.max(20 * 60000, dur * 0.6), Date.now() - 7 * 86400000), hasta = Math.min(t1 + Math.max(10 * 60000, dur * 0.3), Date.now());
    const m = await api.get(`/mediciones${qs({ sensor_id: a.sensor_id, desde: new Date(desde).toISOString(), hasta: new Date(hasta).toISOString() })}`);
    const pts = puntosDe(m), ref = a.regla ? [{ nombre: `límite ${a.regla.operador} ${fmt(a.regla.umbral, 2)}`, valor: a.regla.umbral }] : [];
    const marcas = [{ t: t0, nombre: 'Alerta', clase: 'mal' }, ...(a.resuelta_en ? [{ t: t1, nombre: a.resuelta_auto ? 'Volvió a lo normal' : 'Resuelta', clase: 'bien' }] : [])];
    const ult = pts[pts.length - 1];
    return el('figure', { class: 'graf-fig' }, grafico(pts, { unidad: a.unidad || '', alto: 190, referencias: ref, marcas }),
      el('figcaption', { class: 'graf-pie' }, ult ? `Última lectura del rango: ${fmt(ult.v, 2)} ${a.unidad || ''}. ` : '',
        'Línea roja punteada: límite de la regla. Líneas verticales: momento de la alerta y de su resolución.'));
  });
}
/* Últimas horas de un sensor, para la pantalla «Estado actual». */
function graficoSensor(f, referencias = [], horas = 2) {
  return graficoCargado(`sensor:${f.sensor_id}:${horas}`, 30000, async () => {
    const hasta = new Date(), desde = new Date(hasta.getTime() - horas * 3600 * 1000);
    const m = await api.get(`/mediciones${qs({ sensor_id: f.sensor_id, desde: desde.toISOString(), hasta: hasta.toISOString() })}`);
    return grafico(puntosDe(m), { unidad: f.unidad, alto: 170, referencias });
  });
}
const puntosDe = (resp) => (resp.puntos || []).map((p) => ({ t: Date.parse(p.t), v: Number(p.prom !== undefined ? p.prom : p.valor) }));

/*  formularios genéricos (campos: {nombre, etiqueta, tipo, opciones, requerido, ayuda, vacioNulo}) */
function formulario({ campos, inicial = {}, textoBoton, onEnviar, onCancelar }) {
  const v = { ...inicial }, err = el('div', {});
  const form = el('form', { class: 'panel-form' }, err);
  const rejilla = el('div', { class: 'rejilla-form' });
  for (const c of campos) {
    if (c.tipo === 'multi') {
      v[c.nombre] = v[c.nombre] || [];
      rejilla.append(el('fieldset', {}, el('legend', {}, c.etiqueta), c.opciones.map((o) => el('label', { class: 'campo-check' },
        el('input', { type: 'checkbox', checked: v[c.nombre].includes(o.valor), onchange: (e) => { v[c.nombre] = e.target.checked ? [...v[c.nombre], o.valor] : v[c.nombre].filter((z) => z !== o.valor); } }), ' ', o.texto))));
    } else if (c.tipo === 'checkbox') {
      rejilla.append(el('label', { class: 'campo-check' }, el('input', { type: 'checkbox', checked: !!v[c.nombre], onchange: (e) => { v[c.nombre] = e.target.checked; } }), ' ', c.etiqueta));
    } else {
      const ent = c.tipo === 'select'
        ? el('select', { required: c.requerido, onchange: (e) => { v[c.nombre] = e.target.value; } }, el('option', { value: '' }, c.requerido ? 'Selecciona…' : '—'),
          (c.opciones || []).map((o) => el('option', { value: o.valor, selected: String(v[c.nombre] ?? '') === String(o.valor) }, o.texto)))
        : el('input', { type: c.tipo || 'text', step: c.tipo === 'number' ? 'any' : null, required: c.requerido, value: v[c.nombre] ?? '', autocomplete: c.tipo === 'password' ? 'new-password' : 'off',
          oninput: (e) => { v[c.nombre] = e.target.value; } });
      rejilla.append(el('label', { class: 'campo' }, el('span', {}, c.etiqueta), ent, c.ayuda ? el('small', { class: 'ayuda' }, c.ayuda) : null));
    }
  }
  form.append(rejilla, el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'submit' }, textoBoton),
    onCancelar ? el('button', { type: 'button', class: 'boton-secundario', onclick: onCancelar }, 'Cancelar') : null));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault(); vaciar(err);
    const salida = {};
    for (const c of campos) {
      let x = v[c.nombre];
      if (c.tipo === 'number') x = x === '' || x == null ? undefined : Number(x);
      else if (c.tipo === 'select' && x === '') x = c.vacioNulo ? null : undefined;
      else if ((c.tipo === 'text' || c.tipo === 'password' || c.tipo === 'email' || !c.tipo) && x === '') x = undefined;
      if (x !== undefined) salida[c.nombre] = x;
    }
    try { await onEnviar(salida); } catch (e) { err.append(aviso('error', mensajeError(e))); }
  });
  return form;
}
const opciones = (lista, valor, texto) => (lista || []).map((x) => ({ valor: x[valor], texto: typeof texto === 'function' ? texto(x) : x[texto] }));


/*  Marca: el logo de AquaPlants (reemplaza al emoji de hoja)  */
const logo = (clase = 'logo', completo = false) => el('img', { class: clase, src: completo ? '/static/logo-aquaplants.png' : '/static/logo-marca.png', alt: 'AquaPlants', draggable: 'false' });
const tituloAlerta = (a) => `${a.severidad === 'emergencia' && a.tipo !== 'sin_comunicacion' ? '🚨 EMERGENCIA · ' : ''}${a.problema || (a.tipo === 'sin_comunicacion' ? 'Dispositivo sin comunicación' : 'Valor fuera de rango')}`;

/*  Equipo (celular o navegador) y puente con la app Android  */
function infoAndroid() {
  try { if (window.AquaAndroid && AquaAndroid.info) return JSON.parse(AquaAndroid.info()); } catch { /* no es la app */ }
  return null;
}
/* Vincula la cuenta con el equipo que se está usando. El equipo guarda su id y un secreto (solo en este navegador/celular). */
async function vincularEquipo() {
  try {
    const mapa = JSON.parse(localStorage.getItem('aq_equipos') || '{}'), guardado = mapa[AQ.usuario.id] || {}, app = infoAndroid();
    const r = await api.post('/vinculacion/registrar', { ...(guardado.id ? { id: guardado.id, secreto: guardado.secreto } : {}), plataforma: app ? 'android' : 'web',
      ...(app ? { nombre: app.nombre, ...(app.push_token ? { push_token: app.push_token } : {}) } : {}) });
    AQ.equipo = { id: r.id, nombre: r.nombre };
    if (r.secreto) { mapa[AQ.usuario.id] = { id: r.id, secreto: r.secreto }; localStorage.setItem('aq_equipos', JSON.stringify(mapa)); }
    if (app && !app.push_token && !AQ.reintentoPush) { AQ.reintentoPush = true; setTimeout(vincularEquipo, 20000); }   // el token de avisos del celular puede tardar
  } catch (e) {
    if (e.codigo === 'equipo_revocado') { AQ.mensajeLogin = e.message; detenerAvisos(); AQ.alSalir && AQ.alSalir(); }
    /* cualquier otro error no debe impedir usar la aplicación */
  }
}

/*  Notificaciones: campana, aviso del sistema y sonido  */
const Avisos = { items: [], noLeidas: 0, conocidas: null, temporizador: null, oyentes: [], alAbrir: null };
const iconoCampana = () => {
  const NS = 'http://www.w3.org/2000/svg', s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', '22'); s.setAttribute('height', '22'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', 'M12 2a6 6 0 0 0-6 6v3.6L4.3 15a1 1 0 0 0 .9 1.5h13.6a1 1 0 0 0 .9-1.5L18 11.6V8a6 6 0 0 0-6-6zm0 20a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22z');
  p.setAttribute('fill', 'currentColor'); s.append(p); return s;
};
function pitido(critica) {
  try {
    const c = Avisos.audio || (Avisos.audio = new (window.AudioContext || window.webkitAudioContext)()), o = c.createOscillator(), g = c.createGain();
    o.type = 'sine'; o.frequency.value = critica ? 880 : 660; g.gain.value = 0.08; o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + (critica ? 0.35 : 0.2));
  } catch { /* el navegador bloqueó el sonido hasta que haya una interacción */ }
}
function avisoDelSistema(n) {
  if (window.AquaAndroid && AquaAndroid.notificar) { try { AquaAndroid.notificar(n.titulo, n.mensaje, n.alerta_id || ''); } catch { /* sin puente */ } }
  else if ('Notification' in window && Notification.permission === 'granted') {
    try { const x = new Notification(n.titulo, { body: n.mensaje, icon: '/static/icono-app.png', tag: n.id }); x.onclick = () => { window.focus(); Avisos.alAbrir && Avisos.alAbrir(n); }; } catch { /* no disponible */ }
  }
  pitido(n.critica);
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
}
async function cargarAvisos() {
  const d = await api.get('/notificaciones');
  const nuevas = Avisos.conocidas ? d.items.filter((n) => !Avisos.conocidas.has(n.id) && !n.leida) : [];
  Avisos.conocidas = new Set(d.items.map((n) => n.id)); Avisos.items = d.items; Avisos.noLeidas = d.no_leidas;
  nuevas.forEach(avisoDelSistema);
  Avisos.oyentes.forEach((f) => f(nuevas));
}
function iniciarAvisos() {
  detenerAvisos(); Avisos.conocidas = null;
  const ciclo = () => cargarAvisos().catch(() => {});
  ciclo(); Avisos.temporizador = setInterval(ciclo, RELE ? 8000 : 5000);
}
function detenerAvisos() { clearInterval(Avisos.temporizador); Avisos.temporizador = null; Avisos.conocidas = null; Avisos.items = []; Avisos.noLeidas = 0; }

/* Botón de campana con contador. `alElegir(n)` se llama al tocar una notificación (por ejemplo, para abrir las alertas). */
/* Adónde lleva tocar un aviso: las solicitudes técnicas van al servicio técnico (el técnico no tiene pantalla de alertas). */
const destinoAviso = (n) => (AQ.usuario.rol === 'tecnico' || ['solicitud_tecnica', 'mantencion_actualizada', 'alerta_derivada'].includes(n.tipo) ? 'servicio' : 'alertas');
function campana(alElegir, clase = '') {
  const cuenta = el('span', { class: 'campana-cuenta oculto' }), panel = el('div', { class: 'panel-avisos oculto', role: 'dialog', 'aria-label': 'Notificaciones' });
  const boton = el('button', { type: 'button', class: `campana ${clase}`, 'aria-label': 'Notificaciones', onclick: (e) => { e.stopPropagation(); alternar(); } }, iconoCampana(), cuenta);
  const caja = el('div', { class: 'campana-caja' }, boton, panel);
  Avisos.alAbrir = alElegir;
  function pintar() {
    cuenta.textContent = Avisos.noLeidas > 99 ? '99+' : Avisos.noLeidas; cuenta.classList.toggle('oculto', !Avisos.noLeidas);
    if (panel.classList.contains('oculto')) return;
    const pedirPermiso = !window.AquaAndroid && 'Notification' in window && Notification.permission === 'default';
    vaciar(panel).append(
      el('div', { class: 'panel-avisos-cab' }, el('strong', {}, 'Notificaciones'),
        Avisos.noLeidas ? el('button', { type: 'button', class: 'boton-texto', onclick: async () => { await api.post('/notificaciones/leer', {}); await cargarAvisos(); } }, 'Marcar todas como leídas') : null),
      pedirPermiso ? el('button', { type: 'button', class: 'boton-secundario avisos-permiso', onclick: async () => { await Notification.requestPermission(); pintar(); } }, 'Activar avisos en este navegador') : null,
      el('div', { class: 'panel-avisos-lista' }, Avisos.items.length ? Avisos.items.map((n) => el('button', { type: 'button', class: `aviso-item ${n.leida ? '' : 'sin-leer'} ${n.critica ? 'critica' : ''}`,
        onclick: async () => { alternar(false); if (!n.leida) { try { await api.post('/notificaciones/leer', { ids: [n.id] }); await cargarAvisos(); } catch { /* se reintenta */ } } alElegir && alElegir(n); } },
        el('span', { class: 'aviso-titulo' }, n.titulo), el('span', { class: 'aviso-texto' }, n.mensaje), el('span', { class: 'aviso-hora' }, `${hace(n.creada_en)} · ${fechaHora(n.creada_en)}`)))
        : el('p', { class: 'vacio' }, 'Aún no tienes notificaciones.')));
  }
  function alternar(abrir) {
    const mostrar = abrir === undefined ? panel.classList.contains('oculto') : abrir;
    panel.classList.toggle('oculto', !mostrar); if (mostrar) pintar();
  }
  Avisos.oyentes = [pintar];
  document.addEventListener('click', (e) => { if (!caja.contains(e.target)) panel.classList.add('oculto'); });
  pintar();
  return caja;
}

/* Equipos vinculados a la cuenta (Mi cuenta). */
async function seccionEquipos() {
  const zona = el('div', {}), msg = el('div', {});
  async function cargar() {
    let lista = [];
    try { lista = await api.get('/vinculacion/mis-equipos'); } catch (e) { vaciar(zona).append(aviso('error', mensajeError(e))); return; }
    vaciar(zona).append(lista.length ? lista.map((x) => el('div', { class: 'equipo' },
      el('div', {}, el('strong', {}, x.nombre), x.actual ? el('span', { class: 'etiqueta en_linea equipo-actual' }, 'Este equipo') : null,
        el('div', { class: 'nota' }, `${x.plataforma === 'android' ? 'App Android' : 'Navegador'} · último uso ${fechaHora(x.ultimo_uso)}${x.con_push ? ' · recibe avisos' : ''}${x.revocado ? ' · BLOQUEADO por un administrador' : ''}`)),
      x.actual ? null : el('button', { type: 'button', class: 'boton-texto', onclick: async () => { if (!confirm('¿Desvincular este equipo? Se cerrará su sesión.')) return;
        try { await api.post(`/vinculacion/${x.id}/desvincular`, {}); await cargar(); } catch (e) { vaciar(msg).append(aviso('error', mensajeError(e))); } } }, 'Desvincular')))
      : el('p', { class: 'nota' }, 'Aún no hay equipos vinculados.'));
  }
  await cargar();
  return el('section', { class: 'panel-form tarjeta' }, el('h3', {}, 'Equipos vinculados'),
    el('p', { class: 'ayuda nota' }, 'Cada vez que ingresas, este celular o navegador queda vinculado a tu cuenta. Aquí ves dónde la usas y puedes desvincular un equipo que ya no uses.'), msg, zona);
}

/*  sesión  */
function pantallaLogin(raiz, titulo, alEntrar) {
  AQ.enLogin = true;
  vaciar(raiz);
  const error = el('div', {});
  if (AQ.mensajeLogin) { error.append(aviso('error', AQ.mensajeLogin)); AQ.mensajeLogin = null; }
  const email = el('input', { type: 'email', required: true, autocomplete: 'username', placeholder: 'Correo' });
  const clave = el('input', { type: 'password', required: true, autocomplete: 'current-password', placeholder: 'Contraseña' });
  const codigo = el('input', { type: 'text', inputmode: 'numeric', pattern: '\\d{6}', maxlength: 6, placeholder: 'Código de 6 dígitos', autocomplete: 'one-time-code', class: 'oculto' });
  const boton = el('button', { class: 'boton', type: 'submit' }, 'Ingresar');
  const form = el('form', { class: 'login' }, el('div', { class: 'login-logo' }, logo('login-img', true)), el('h1', {}, titulo), el('p', { class: 'sub' }, 'Plataforma de monitoreo y gestión de cultivos aeropónicos'),
    email, clave, codigo, boton, error, el('p', { class: 'nota' }, 'Plataforma de demostración: todos los datos son simulados.'));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault(); boton.disabled = true; vaciar(error);
    try {
      const d = await api.post('/auth/login', { email: email.value.trim(), password: clave.value, ...(codigo.value ? { codigo_totp: codigo.value } : {}) });
      AQ.usuario = d.usuario; AQ.csrf = d.csrfToken; AQ.enLogin = false; AQ.reintentoPush = false; vincularEquipo(); iniciarAvisos(); alEntrar();
    } catch (e) {
      if (e.codigo === 'totp_requerido') { codigo.classList.remove('oculto'); codigo.required = true; codigo.focus(); error.append(aviso('ok', e.message)); }
      else error.append(aviso('error', e.message));
    } finally { boton.disabled = false; }
  });
  raiz.append(el('div', { class: 'login-caja' }, form));
  email.focus();
}
async function iniciarSesion(raiz, titulo, alEntrar) {
  AQ.alSalir = () => pantallaLogin(raiz, titulo, alEntrar);
  try { const d = await api.get('/auth/me'); AQ.usuario = d.usuario; AQ.csrf = d.csrfToken; vincularEquipo(); iniciarAvisos(); alEntrar(); } catch { if (!AQ.enLogin) pantallaLogin(raiz, titulo, alEntrar);  }
}
async function cerrarSesion() {
  detenerAvisos(); try { await api.post('/auth/logout'); } catch { /* ya cerrada */ }
  Rele.token = null; _local.guardar('aq_token', null); AQ.csrf = null; AQ.alSalir && AQ.alSalir();
}
