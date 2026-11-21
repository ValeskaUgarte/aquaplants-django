/* Secciones de administración (solo rol Administrador). Las usan la web (Administración) y la consola /admin.
   Mismo patrón en todas: tabla + formulario. Regla del proyecto: UN DISPOSITIVO POR CULTIVO. */
const nombreDe = (lista, id, campo = 'nombre') => (lista.find((x) => x.id === id) || {})[campo];
const botonTexto = (t, f) => el('button', { type: 'button', class: 'boton-texto', onclick: f }, t);

function marco(z) {
  const msg = el('div', {}), formZona = el('div', {}), cuerpo = el('div', {});
  vaciar(z).append(msg, formZona, cuerpo);
  const ok = (t) => vaciar(msg).append(aviso('ok', t));
  const mal = (e) => vaciar(msg).append(aviso('error', mensajeError(e)));
  const mostrarForm = (f) => vaciar(formZona).append(f);
  const cerrarForm = () => vaciar(formZona);
  return { msg, cuerpo, ok, mal, mostrarForm, cerrarForm };
}
const accion = (m, fn, texto, recargar) => async () => { try { await fn(); m.ok(texto); await recargar(); } catch (e) { m.mal(e); } };

/*  Usuarios  */
async function secUsuarios(z) {
  const m = marco(z), [usuarios, insts, disp] = await Promise.all([api.get('/usuarios'), api.get('/instalaciones'), api.get('/dispositivos')]);
  const roles = Object.entries(ROL).map(([valor, texto]) => ({ valor, texto }));
  const campoInst = { nombre: 'instalaciones', etiqueta: 'Instalaciones asignadas (Encargado de cultivo)', tipo: 'multi', opciones: opciones(insts, 'id', 'nombre') };
  const campoDisp = { nombre: 'dispositivos', etiqueta: 'Dispositivos a cargo (recibe sus alertas; también sirve para un hogar o un cultivo pequeño)', tipo: 'multi',
    opciones: disp.map((d) => ({ valor: d.id, texto: `${d.cultivo_nombre || 'Sin cultivo'} · ${d.codigo} (${nombreDe(insts, d.instalacion_id)})` })) };
  const recargar = () => secUsuarios(z);
  const nuevo = () => m.mostrarForm(formulario({ textoBoton: 'Crear usuario', onCancelar: m.cerrarForm, campos: [
    { nombre: 'email', etiqueta: 'Correo', tipo: 'email', requerido: true }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true },
    { nombre: 'rol', etiqueta: 'Rol', tipo: 'select', opciones: roles, requerido: true, ayuda: 'El técnico o servicio técnico recibe solicitudes de arreglo, reemplazo, retiro y mantención; no ve alertas ni cultivos.' },
    { nombre: 'password', etiqueta: 'Contraseña inicial', tipo: 'password', requerido: true, ayuda: 'Mínimo 12 caracteres con mayúscula, minúscula, número y símbolo.' }, campoInst, campoDisp],
    onEnviar: async (d) => { await api.post('/usuarios', d); m.ok('Usuario creado.'); await recargar(); } }));
  const editar = (u) => m.mostrarForm(formulario({ textoBoton: 'Guardar cambios', onCancelar: m.cerrarForm,
    inicial: { nombre: u.nombre, rol: u.rol, activo: u.activo, instalaciones: u.instalaciones, dispositivos: u.dispositivos, suspension_motivo: u.suspension ? u.suspension.motivo : '' }, campos: [
      { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text' }, { nombre: 'rol', etiqueta: 'Rol', tipo: 'select', opciones: roles },
      { nombre: 'activo', etiqueta: 'Cuenta activa', tipo: 'checkbox', ayuda: 'Desmárcala para suspender la cuenta sin borrarla.' }, { nombre: 'suspension_motivo', etiqueta: 'Motivo de la suspensión', tipo: 'text' },
      { nombre: 'password', etiqueta: 'Nueva contraseña (opcional)', tipo: 'password', ayuda: 'Al cambiarla se cierran las sesiones del usuario.' }, campoInst, campoDisp],
    onEnviar: async (d) => { await api.patch(`/usuarios/${u.id}`, d); m.ok('Usuario actualizado.'); await recargar(); } }));
  const suspender = (u) => m.mostrarForm(formulario({ textoBoton: 'Suspender cuenta', onCancelar: m.cerrarForm, campos: [{ nombre: 'suspension_motivo', etiqueta: `Motivo para suspender a ${u.nombre}`, tipo: 'text', requerido: true }],
    onEnviar: async (d) => { await api.patch(`/usuarios/${u.id}`, { activo: false, suspension_motivo: d.suspension_motivo }); m.ok(`${u.nombre} quedó suspendido: no puede entrar, pero se conserva su historial.`); await recargar(); } }));
  const eliminar = async (u) => {
    if (!window.confirm(`¿Eliminar la cuenta de ${u.nombre}?\n\nNo podrá volver a entrar y su correo quedará libre. Su nombre se conserva en las bitácoras e informes. ${u.rol === 'tecnico' ? 'Sus solicitudes abiertas vuelven a la bolsa de técnicos.' : ''}`)) return;
    try { await api.del(`/usuarios/${u.id}`); m.ok(`Cuenta de ${u.nombre} eliminada.`); await recargar(); } catch (e) { m.mal(e); }
  };
  const nombresDisp = (ids) => ids.map((id) => { const d = disp.find((x) => x.id === id); return d ? `${d.cultivo_nombre || 'Sin cultivo'} · ${d.codigo}` : null; }).filter(Boolean);
  m.cuerpo.append(el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nuevo }, 'Nuevo usuario')),
    el('p', { class: 'ayuda' }, 'Registro de personas con acceso. Suspender impide entrar pero conserva todo; eliminar cierra la cuenta para siempre (se conserva el nombre en el historial). Vincula a cada persona con los dispositivos que tiene a cargo: sus alertas le llegan a ella.'),
    tabla([{ titulo: 'Nombre', campo: 'nombre' }, { titulo: 'Correo', campo: 'email' }, { titulo: 'Rol', ver: (f) => ROL[f.rol] },
      { titulo: 'Estado', ver: (f) => (f.activo ? 'Activo' : [el('strong', { class: 'alerta-n' }, 'Suspendido'), f.suspension ? el('div', { class: 'meta suave' }, `${f.suspension.motivo || 'sin motivo'}${f.suspension.por ? ` · por ${f.suspension.por}` : ''}`) : null]) },
      { titulo: '2FA', ver: (f) => (f.totp_activo ? 'Sí' : 'No') },
      { titulo: 'A cargo', ver: (f) => (f.rol === 'tecnico' ? el('span', { class: 'suave' }, 'recibe solicitudes') : f.rol !== 'encargado_cultivo' ? 'todo' : [
        ...nombresDisp(f.dispositivos).map((t) => el('span', { class: 'chip-var' }, t)), nombresDisp(f.dispositivos).length ? null : el('span', { class: 'suave' }, f.instalaciones.map((i) => nombreDe(insts, i)).join(', ') || 'nada asignado')]) },
      { titulo: 'Último ingreso', ver: (f) => fechaHora(f.ultimo_login) },
      { titulo: '', ver: (f) => [botonTexto('Editar', () => editar(f)), ' ', f.activo ? botonTexto('Suspender', () => suspender(f)) : botonTexto('Reactivar', accion(m, () => api.patch(`/usuarios/${f.id}`, { activo: true }), 'Cuenta reactivada.', recargar)),
        ' ', botonTexto('Eliminar', () => eliminar(f))] }], usuarios));
}

/*  Instalaciones  */
async function secInstalaciones(z) {
  const m = marco(z), [datos, tecs] = await Promise.all([api.get('/instalaciones'), api.get('/tecnicos')]), recargar = () => secInstalaciones(z);
  const campoTec = { nombre: 'tecnico_id', etiqueta: 'Técnico o servicio técnico asignado (conexión directa)', tipo: 'select', ayuda: 'Las solicitudes de esta instalación le llegan solo a él. Sin técnico asignado llegan a todos los técnicos.',
    opciones: [{ valor: '', texto: 'Ninguno (a todos los técnicos)' }, ...tecs.map((t) => ({ valor: t.id, texto: t.nombre }))] };
  const nueva = () => m.mostrarForm(formulario({ textoBoton: 'Crear instalación', onCancelar: m.cerrarForm, campos: [
    { nombre: 'codigo', etiqueta: 'Código (id_sistema)', tipo: 'text', requerido: true, ayuda: 'Letras, números, guion. Identifica a la instalación.' },
    { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true }, { nombre: 'ubicacion', etiqueta: 'Ubicación', tipo: 'text' },
    { nombre: 'descripcion', etiqueta: 'Descripción', tipo: 'text' }, campoTec],
    onEnviar: async (d) => { await api.post('/instalaciones', d); await recargar(); } }));
  const editar = (i) => m.mostrarForm(formulario({ textoBoton: 'Guardar cambios', onCancelar: m.cerrarForm, inicial: { ...i, tecnico_id: i.tecnico_id || '' }, campos: [
    { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text' }, { nombre: 'ubicacion', etiqueta: 'Ubicación', tipo: 'text' }, { nombre: 'descripcion', etiqueta: 'Descripción', tipo: 'text' }, { nombre: 'activa', etiqueta: 'Instalación activa', tipo: 'checkbox' }, campoTec],
    onEnviar: async (d) => { await api.patch(`/instalaciones/${i.id}`, d); await recargar(); } }));
  m.cuerpo.append(el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nueva }, 'Nueva instalación')),
    tabla([{ titulo: 'Código', campo: 'codigo' }, { titulo: 'Nombre', campo: 'nombre' }, { titulo: 'Ubicación', ver: (f) => f.ubicacion || '—' }, { titulo: 'Descripción', ver: (f) => f.descripcion || '—' },
      { titulo: 'Técnico asignado', ver: (f) => f.tecnico_nombre || el('span', { class: 'suave' }, 'a todos') },
      { titulo: 'Estado', ver: (f) => (f.activa ? 'Activa' : 'Inactiva') }, { titulo: '', ver: (f) => botonTexto('Editar', () => editar(f)) }], datos));
}

/*  Cultivos y plantas  */
async function secCultivos(z) {
  const m = marco(z), [insts, cultivos, plantas, disp] = await Promise.all([api.get('/instalaciones'), api.get('/cultivos'), api.get('/plantas'), api.get('/dispositivos')]);
  const recargar = () => secCultivos(z);
  const nuevoC = () => m.mostrarForm(formulario({ textoBoton: 'Crear cultivo', onCancelar: m.cerrarForm, campos: [
    { nombre: 'instalacion_id', etiqueta: 'Instalación', tipo: 'select', opciones: opciones(insts, 'id', 'nombre'), requerido: true },
    { nombre: 'nombre', etiqueta: 'Nombre del cultivo', tipo: 'text', requerido: true, ayuda: 'Ejemplo: Lechuga, Albahaca, Tomate cherry.' }, { nombre: 'descripcion', etiqueta: 'Descripción', tipo: 'text' }],
    onEnviar: async (d) => { await api.post('/cultivos', d); await recargar(); } }));
  const nuevaP = (c) => m.mostrarForm(formulario({ textoBoton: `Agregar planta a ${c.nombre}`, onCancelar: m.cerrarForm, campos: [
    { nombre: 'codigo', etiqueta: 'Código', tipo: 'text', requerido: true }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true }],
    onEnviar: async (d) => { await api.post('/plantas', { ...d, cultivo_id: c.id }); await recargar(); } }));
  m.cuerpo.append(el('p', { class: 'ayuda' }, 'Cada cultivo se monitorea con UN dispositivo. Primero crea el cultivo y luego registra su dispositivo en «Dispositivos y sensores».'),
    el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nuevoC }, 'Nuevo cultivo')),
    ...cultivos.map((c) => { const d = disp.find((x) => x.cultivo_id === c.id); return el('section', { class: 'bloque' },
      el('h3', {}, c.nombre, el('small', {}, ` (${nombreDe(insts, c.instalacion_id)}) · ${d ? `dispositivo ${d.codigo}` : 'SIN dispositivo asignado'}`)),
      el('div', { class: 'acciones' }, botonTexto('Agregar planta', () => nuevaP(c)),
        botonTexto('Eliminar cultivo', () => confirm('¿Eliminar este cultivo y sus plantas?') && accion(m, () => api.del(`/cultivos/${c.id}`), 'Cultivo eliminado.', recargar)())),
      tabla([{ titulo: 'Código', campo: 'codigo' }, { titulo: 'Nombre', campo: 'nombre' },
        { titulo: '', ver: (p) => botonTexto('Eliminar', accion(m, () => api.del(`/plantas/${p.id}`), 'Planta eliminada.', recargar)) }], plantas.filter((p) => p.cultivo_id === c.id), 'Sin plantas registradas.')); }));
}

/*  Dispositivos y sensores  */
let _mensajeSensores = null, _filtroSensores = '';     // el mensaje de «sensores creados» y el cultivo mostrado sobreviven a la recarga de la pantalla
async function secDispositivos(z) {
  const m = marco(z), [insts, disp, sens, tipos, cultivos] = await Promise.all([api.get('/instalaciones'), api.get('/dispositivos'), api.get('/sensores'), api.get('/tipos-sensor'), api.get('/cultivos')]);
  const recargar = () => secDispositivos(z), secretoZona = el('div', {});
  if (_mensajeSensores) { vaciar(m.msg).append(..._mensajeSensores); _mensajeSensores = null; }
  const mostrarSecreto = (s) => vaciar(secretoZona).append(el('div', { class: 'aviso aviso-warn', role: 'alert' }, el('strong', {}, 'Secreto del dispositivo (se muestra una sola vez): '), el('code', { class: 'secreto' }, s), ' ',
    botonTexto('Ya lo guardé', () => vaciar(secretoZona))));
  const libres = cultivos.filter((c) => !disp.some((d) => d.cultivo_id === c.id));
  const nuevoD = () => m.mostrarForm(formulario({ textoBoton: 'Crear dispositivo', onCancelar: m.cerrarForm, campos: [
    { nombre: 'cultivo_id', etiqueta: 'Cultivo que monitorea (solo cultivos sin dispositivo)', tipo: 'select', requerido: true, opciones: libres.map((c) => ({ valor: c.id, texto: `${c.nombre} — ${nombreDe(insts, c.instalacion_id)}` })) },
    { nombre: 'codigo', etiqueta: 'Código (id_dispositivo)', tipo: 'text', requerido: true }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true }],
    onEnviar: async (d) => { const c = cultivos.find((x) => x.id === d.cultivo_id); const r = await api.post('/dispositivos', { ...d, instalacion_id: c.instalacion_id }); await recargar(); mostrarSecreto(r.secreto); } }));
  const nuevoS = () => {
    const elegidos = new Map(), err = el('div', {});                          // tipo_sensor_id -> { codigo, nombre }
    const zonaTipos = el('div', { class: 'tipos-lista' }), zonaFilas = el('div', { class: 'tipos-filas' });
    let dispId = _filtroSensores && disp.some((d) => d.id === _filtroSensores) ? _filtroSensores : '';
    const sugerir = (t) => {                                                    // código sugerido: 3 letras del tipo + número libre en ese dispositivo
      const usados = new Set([...sens.filter((s) => s.dispositivo_id === dispId).map((s) => s.codigo), ...[...elegidos.values()].map((v) => v.codigo)]);
      const base = (t.tipo_variable || t.nombre).normalize('NFD').replace(/[^A-Za-z]/g, '').slice(0, 3).toUpperCase() || 'SEN';
      let n = 1; while (usados.has(base + n)) n++; return base + n;
    };
    const pintar = () => {
      const yaTiene = (t) => sens.filter((s) => s.dispositivo_id === dispId && s.tipo_sensor_id === t.id).length;
      vaciar(zonaTipos).append(dispId ? tipos.map((t) => el('label', { class: `campo-check tipo-op ${elegidos.has(t.id) ? 'sel' : ''}` },
        el('input', { type: 'checkbox', checked: elegidos.has(t.id), onchange: (e) => { if (e.target.checked) elegidos.set(t.id, { codigo: sugerir(t), nombre: t.nombre }); else elegidos.delete(t.id); pintar(); } }),
        el('span', {}, el('strong', {}, t.nombre), ` (${t.unidad})`, yaTiene(t) ? el('small', { class: 'suave' }, ` · ya tiene ${yaTiene(t)}`) : null))) : el('p', { class: 'suave' }, 'Primero elige el cultivo.'));
      vaciar(zonaFilas).append(elegidos.size ? el('div', { class: 'tabla' }, el('table', {}, el('thead', {}, el('tr', {}, ['Variable', 'Código (id_sensor)', 'Nombre'].map((h) => el('th', {}, h)))),
        el('tbody', {}, [...elegidos].map(([id, v]) => { const t = tipos.find((x) => x.id === id); return el('tr', {}, el('td', {}, `${t.nombre} (${t.unidad})`),
          el('td', {}, el('input', { type: 'text', required: true, maxlength: 40, value: v.codigo, oninput: (e) => { v.codigo = e.target.value.trim(); } })),
          el('td', {}, el('input', { type: 'text', required: true, maxlength: 120, value: v.nombre, oninput: (e) => { v.nombre = e.target.value; } }))); })))) : null);
    };
    const dispSel = el('select', { required: true, onchange: (e) => { dispId = e.target.value; elegidos.clear(); pintar(); } }, el('option', { value: '' }, 'Selecciona un cultivo…'),
      disp.map((d) => el('option', { value: d.id, selected: d.id === dispId }, `${d.cultivo_nombre || 'Sin cultivo'} — ${nombreDe(insts, d.instalacion_id)} · ${d.codigo}`)));
    const enviar = el('button', { class: 'boton', type: 'submit' }, 'Crear sensores');
    const form = el('form', { class: 'panel-form ancho' }, err, el('h4', {}, 'Nuevos sensores'),
      el('label', { class: 'campo' }, el('span', {}, 'Cultivo (el sensor mide el cultivo de su dispositivo)'), dispSel),
      el('div', { class: 'campo' }, el('span', {}, 'Variables que mide (marca todas las que necesites)'), zonaTipos), zonaFilas,
      el('div', { class: 'acciones' }, enviar, el('button', { type: 'button', class: 'boton-secundario', onclick: m.cerrarForm }, 'Cancelar')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault(); vaciar(err);
      if (!dispId || !elegidos.size) { err.append(aviso('error', 'Elige el cultivo y marca al menos una variable.')); return; }
      enviar.disabled = true; enviar.textContent = 'Creando…';
      const hechos = [], fallos = [];
      for (const [tid, v] of elegidos) {                                        // uno por uno: si uno falla, los demás igual se crean y se dice cuál
        try { await api.post('/sensores', { dispositivo_id: dispId, tipo_sensor_id: tid, codigo: v.codigo, nombre: v.nombre || v.codigo }); hechos.push(v.codigo); }
        catch (e) { fallos.push(`${v.codigo}: ${mensajeError(e)}`); }
      }
      const d = disp.find((x) => x.id === dispId), donde = `${d.cultivo_nombre || 'el cultivo'} (${d.codigo})`;
      if (!fallos.length) { _filtroSensores = dispId; _mensajeSensores = [aviso('ok', `Se ${hechos.length === 1 ? 'creó 1 sensor' : `crearon ${hechos.length} sensores`} en ${donde}: ${hechos.join(', ')}. Ya figuran en la tabla «Sensores» de abajo.`)]; await recargar(); return; }
      if (hechos.length) { _filtroSensores = dispId; _mensajeSensores = [aviso('ok', `Se crearon en ${donde}: ${hechos.join(', ')}.`), aviso('error', `No se pudieron crear: ${fallos.join(' · ')}`)]; await recargar(); return; }
      err.append(aviso('error', `No se creó ningún sensor. ${fallos.join(' · ')}`)); enviar.disabled = false; enviar.textContent = 'Crear sensores';
    });
    pintar(); m.mostrarForm(form);
  };
  m.cuerpo.append(secretoZona, el('p', { class: 'ayuda' }, 'Regla del proyecto: un dispositivo por cultivo. Los sensores de un dispositivo miden el cultivo al que pertenece.'),
    el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nuevoD, disabled: !libres.length, title: libres.length ? '' : 'Todos los cultivos ya tienen dispositivo' }, 'Nuevo dispositivo'),
      el('button', { class: 'boton-secundario', type: 'button', onclick: nuevoS, disabled: !disp.length }, 'Nuevo sensor (varias variables a la vez)')),
    el('h3', {}, 'Dispositivos'),
    tabla([{ titulo: 'Cultivo', ver: (f) => el('strong', {}, f.cultivo_nombre || '—') }, { titulo: 'Dispositivo', ver: (f) => `${f.codigo} · ${f.nombre}` }, { titulo: 'Instalación', ver: (f) => nombreDe(insts, f.instalacion_id) },
      { titulo: 'Sensores', ver: (f) => { const l = sens.filter((s) => s.dispositivo_id === f.id); return l.length ? l.map((s) => el('span', { class: 'chip-var', title: s.nombre }, s.tipo_nombre)) : el('span', { class: 'suave' }, 'ninguno'); } },
      { titulo: 'A cargo', ver: (f) => (f.responsables || []).length ? f.responsables.map((r) => r.nombre).join(', ') : el('span', { class: 'suave' }, 'la instalación') },
      { titulo: 'Ubicación', ver: (f) => (f.ubicacion ? [el('a', { href: enlaceMapa(f.ubicacion), target: '_blank', rel: 'noopener' }, `${f.ubicacion.lat.toFixed(4)}, ${f.ubicacion.lng.toFixed(4)}`),
        el('div', { class: 'meta suave' }, `${f.ubicacion.origen === 'manual' ? 'a mano' : 'GPS'} · ${hace(f.ubicacion.actualizada_en)}`)] : el('span', { class: 'suave' }, 'sin GPS')) },
      { titulo: 'Conexión', ver: (f) => estado(f.estado_conexion) }, { titulo: 'Última comunicación', ver: (f) => fechaHora(f.ultima_comunicacion) }, { titulo: 'Activo', ver: (f) => (f.activo ? 'Sí' : 'No') },
      { titulo: 'Acciones', ver: (f) => el('span', { class: 'acciones' }, botonTexto(f.activo ? 'Desactivar' : 'Activar', accion(m, () => api.patch(`/dispositivos/${f.id}`, { activo: !f.activo }), 'Dispositivo actualizado.', recargar)),
        botonTexto('Rotar secreto', async () => { if (confirm('El secreto anterior dejará de funcionar. ¿Continuar?')) { try { const r = await api.post(`/dispositivos/${f.id}/rotar-secreto`); mostrarSecreto(r.secreto); } catch (e) { m.mal(e); } } })) }], disp),
    el('h3', { id: 'tabla-sensores' }, `Sensores (${sens.length})`),
    el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Mostrar los sensores de'), el('select', { onchange: (e) => { _filtroSensores = e.target.value; recargar(); } }, el('option', { value: '' }, 'Todos los cultivos'),
      disp.map((d) => el('option', { value: d.id, selected: d.id === _filtroSensores }, `${d.cultivo_nombre || 'Sin cultivo'} · ${d.codigo}`)))),
    tabla([{ titulo: 'Cultivo', ver: (f) => f.cultivo_nombre || '—' }, { titulo: 'Dispositivo', campo: 'dispositivo_codigo' }, { titulo: 'Sensor', ver: (f) => `${f.codigo} · ${f.nombre}` }, { titulo: 'Variable', campo: 'tipo_nombre' },
      { titulo: 'Unidad', campo: 'unidad' }, { titulo: 'Activo', ver: (f) => (f.activo ? 'Sí' : 'No') },
      { titulo: '', ver: (f) => botonTexto(f.activo ? 'Desactivar' : 'Activar', accion(m, () => api.patch(`/sensores/${f.id}`, { activo: !f.activo }), 'Sensor actualizado.', recargar)) }],
      sens.filter((s) => !_filtroSensores || s.dispositivo_id === _filtroSensores), 'Este cultivo aún no tiene sensores: usa «Nuevo sensor».'));
}

/*  Tipos de sensor  */
async function secTipos(z) {
  const m = marco(z), datos = await api.get('/tipos-sensor'), recargar = () => secTipos(z);
  const nuevo = () => m.mostrarForm(formulario({ textoBoton: 'Crear tipo', onCancelar: m.cerrarForm, campos: [
    { nombre: 'codigo', etiqueta: 'Código', tipo: 'text', requerido: true, ayuda: 'minúsculas, números y guion bajo' }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true },
    { nombre: 'tipo_variable', etiqueta: 'Tipo de variable', tipo: 'text', requerido: true }, { nombre: 'unidad', etiqueta: 'Unidad', tipo: 'text', requerido: true },
    { nombre: 'valor_min', etiqueta: 'Límite mínimo', tipo: 'number' }, { nombre: 'valor_max', etiqueta: 'Límite máximo', tipo: 'number' }],
    onEnviar: async (d) => { await api.post('/tipos-sensor', d); await recargar(); } }));
  m.cuerpo.append(el('p', { class: 'ayuda' }, 'Los límites de validación son opcionales: una medición fuera de ellos se rechaza como dato erróneo.'),
    el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nuevo }, 'Nuevo tipo de sensor')),
    tabla([{ titulo: 'Código', campo: 'codigo' }, { titulo: 'Nombre', campo: 'nombre' }, { titulo: 'Variable', campo: 'tipo_variable' }, { titulo: 'Unidad', campo: 'unidad' },
      { titulo: 'Mínimo', num: true, ver: (f) => fmt(f.valor_min) }, { titulo: 'Máximo', num: true, ver: (f) => fmt(f.valor_max) }], datos));
}

/*  Reglas de alerta  */
async function secReglas(z) {
  const m = marco(z), [reglas, tipos, sens] = await Promise.all([api.get('/reglas-alerta'), api.get('/tipos-sensor'), api.get('/sensores')]), recargar = () => secReglas(z);
  const etiquetaSensor = (s) => `${s.cultivo_nombre || 'Sin cultivo'} · ${s.dispositivo_codigo} · ${s.tipo_nombre}`;
  const nueva = () => m.mostrarForm(formulario({ textoBoton: 'Crear regla', onCancelar: m.cerrarForm, campos: [
    { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true },
    { nombre: 'sensor_id', etiqueta: 'Aplica a un sensor concreto (recomendado: cada cultivo tiene sus niveles)', tipo: 'select', vacioNulo: true, opciones: sens.map((s) => ({ valor: s.id, texto: etiquetaSensor(s) })) },
    { nombre: 'tipo_sensor_id', etiqueta: '…o a todos los sensores de un tipo', tipo: 'select', vacioNulo: true, opciones: opciones(tipos, 'id', 'nombre') },
    { nombre: 'operador', etiqueta: 'Condición', tipo: 'select', requerido: true, opciones: [{ valor: '>', texto: 'mayor que' }, { valor: '>=', texto: 'mayor o igual que' }, { valor: '<', texto: 'menor que' }, { valor: '<=', texto: 'menor o igual que' }] },
    { nombre: 'umbral', etiqueta: 'Umbral', tipo: 'number', requerido: true },
    { nombre: 'severidad', etiqueta: 'Severidad', tipo: 'select', opciones: [{ valor: 'advertencia', texto: 'Advertencia' }, { valor: 'emergencia', texto: 'Emergencia' }] }],
    onEnviar: async (d) => { await api.post('/reglas-alerta', d); await recargar(); } }));
  m.cuerpo.append(el('p', { class: 'ayuda' }, 'Solo el Administrador define los umbrales. Una alerta se abre cuando una medición cumple la regla y se resuelve cuando deja de cumplirla. Elige un sensor o un tipo (solo uno).'),
    el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nueva }, 'Nueva regla')),
    tabla([{ titulo: 'Nombre', campo: 'nombre' }, { titulo: 'Aplica a', ver: (f) => (f.sensor_id ? etiquetaSensor(sens.find((s) => s.id === f.sensor_id) || {}) : `Todos los sensores de «${f.tipo_nombre}»`) },
      { titulo: 'Condición', ver: (f) => `${f.operador} ${f.umbral}` }, { titulo: 'Severidad', ver: (f) => (f.severidad === 'emergencia' ? 'Emergencia' : 'Advertencia') },
      { titulo: 'Estado', ver: (f) => (f.activa ? 'Activa' : 'Inactiva') },
      { titulo: 'Acciones', ver: (f) => el('span', { class: 'acciones' }, botonTexto(f.activa ? 'Desactivar' : 'Activar', accion(m, () => api.patch(`/reglas-alerta/${f.id}`, { activa: !f.activa }), 'Regla actualizada.', recargar)),
        botonTexto(f.severidad === 'emergencia' ? 'Marcar advertencia' : 'Marcar emergencia', accion(m, () => api.patch(`/reglas-alerta/${f.id}`, { severidad: f.severidad === 'emergencia' ? 'advertencia' : 'emergencia' }), 'Severidad actualizada.', recargar)),
        botonTexto('Eliminar', () => confirm('¿Eliminar la regla?') && accion(m, () => api.del(`/reglas-alerta/${f.id}`), 'Regla eliminada.', recargar)())) }], reglas));
}

/*  Parámetros del sistema  */
async function secParametros(z) {
  const m = marco(z), datos = await api.get('/parametros'), recargar = () => secParametros(z);
  datos.forEach((p) => {
    const texto = p.clave === 'google_maps_api_key', entrada = el('input', texto ? { type: 'text', value: p.valor, maxlength: 120, autocomplete: 'off', placeholder: 'AIza… (vacío = mapa simple)' } : { type: 'number', min: 0, value: p.valor, required: true });
    const f = el('form', { class: 'panel-form' }, el('label', { class: 'campo' }, el('span', {}, p.clave.replaceAll('_', ' ')), entrada, el('small', { class: 'ayuda' }, p.descripcion)), el('button', { class: 'boton', type: 'submit' }, 'Guardar'));
    f.addEventListener('submit', (e) => { e.preventDefault(); accion(m, () => api.put(`/parametros/${p.clave}`, { valor: String(entrada.value) }), 'Parámetro guardado.', recargar)(); });
    m.cuerpo.append(f);
  });
}

/*  Carga manual  */
async function secManual(z) {
  const m = marco(z), sens = await api.get('/sensores');
  const f = { sensor_id: '', valor: '', timestamp: '' };
  const form = el('form', { class: 'panel-form' }, el('p', { class: 'ayuda' }, 'Registra una medición a mano (origen «manual»). Se valida y evalúa contra las reglas igual que una medición de dispositivo.'),
    el('label', { class: 'campo' }, el('span', {}, 'Cultivo · dispositivo · variable'), el('select', { required: true, onchange: (e) => { f.sensor_id = e.target.value; } }, el('option', { value: '' }, 'Selecciona…'),
      sens.map((s) => el('option', { value: s.id }, `${s.cultivo_nombre || 'Sin cultivo'} · ${s.dispositivo_codigo} · ${s.tipo_nombre} (${s.unidad})`)))),
    el('label', { class: 'campo' }, el('span', {}, 'Valor'), el('input', { type: 'number', step: 'any', required: true, oninput: (e) => { f.valor = e.target.value; } })),
    el('label', { class: 'campo' }, el('span', {}, 'Fecha y hora (opcional)'), el('input', { type: 'datetime-local', oninput: (e) => { f.timestamp = e.target.value; } }), el('small', { class: 'ayuda' }, 'Si se omite se usa el momento actual.')),
    el('button', { class: 'boton', type: 'submit' }, 'Registrar medición'));
  form.addEventListener('submit', async (e) => { e.preventDefault(); try { await api.post('/mediciones/manual', { sensor_id: f.sensor_id, valor: Number(f.valor), ...(f.timestamp ? { timestamp: new Date(f.timestamp).toISOString() } : {}) }); m.ok('Medición registrada con origen manual.'); } catch (err) { m.mal(err); } });
  m.cuerpo.append(form);
}

/*  Auditoría  */
async function secAuditoria(z) {
  const m = marco(z), datos = await api.get('/auditoria'), recargar = () => secAuditoria(z), verZona = el('div', {});
  m.cuerpo.append(el('div', { class: 'acciones' }, el('button', { class: 'boton-secundario', type: 'button', onclick: recargar }, 'Actualizar'),
    el('button', { class: 'boton', type: 'button', onclick: async () => { const v = await api.get('/auditoria/verificar'); vaciar(verZona).append(aviso(v.ok ? 'ok' : 'error', v.ok ? `Registro íntegro (${v.filas} entradas encadenadas).` : `Se detectó una alteración en la entrada ${v.filaCorrupta}.`)); } }, 'Verificar integridad del registro')),
    verZona, tabla([{ titulo: 'Fecha', ver: (f) => fechaHora(f.ocurrido_en) }, { titulo: 'Usuario', ver: (f) => f.usuario || '—' }, { titulo: 'Acción', campo: 'accion' }, { titulo: 'Entidad', ver: (f) => f.entidad || '—' }, { titulo: 'IP', ver: (f) => f.ip || '—' }], datos));
}

/*  Solo lectura (consola /admin) */
async function secAlertasTodas(z) {
  const m = marco(z), datos = await api.get(`/alertas${qs({ limite: 200 })}`);
  m.cuerpo.append(tabla([{ titulo: 'Estado', ver: (f) => estado(f.estado) }, { titulo: 'Cultivo', ver: (f) => f.cultivo || '—' }, { titulo: 'Detalle', campo: 'mensaje' }, { titulo: 'Instalación', campo: 'instalacion' },
    { titulo: 'Origen', ver: (f) => [f.dispositivo_codigo, f.sensor_codigo].filter(Boolean).join(' · ') }, { titulo: 'Creada', ver: (f) => fechaHora(f.creada_en) }], datos));
}
async function secEventosTodos(z) {
  const m = marco(z), datos = await api.get('/eventos');
  m.cuerpo.append(tabla([{ titulo: 'Fecha', ver: (f) => fechaHora(f.ocurrido_en) }, { titulo: 'Tipo', ver: (f) => TIPO_EVENTO[f.tipo] || f.tipo }, { titulo: 'Instalación', campo: 'instalacion' },
    { titulo: 'Dispositivo', ver: (f) => f.dispositivo_codigo || '—' }, { titulo: 'Descripción', campo: 'descripcion' }], datos));
}


/*  Seguimiento de alertas (administrador y supervisor)  */
const minutosATexto = (m) => (m < 60 ? `${m} min` : m < 1440 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.floor(m / 1440)} d ${Math.floor((m % 1440) / 60)} h`);
function comentarios(f) {
  return el('div', {}, f.reconocida_por ? el('div', { class: 'meta suave' }, `Reconocida por ${f.reconocida_por}.`) : null,
    f.revision_por ? el('div', {}, el('strong', {}, `${f.revision_por}: `), f.revision_solucionado ? 'quedó solucionado. ' : 'NO quedó solucionado. ', f.revision_nota) : null,
    f.verificacion_por ? el('div', {}, el('strong', {}, `${f.verificacion_por}: `), f.verificacion_aprobada ? 'confirmó la solución. ' : 'rechazó la revisión. ', f.verificacion_nota || '') : null,
    f.resuelta_auto ? el('div', { class: 'meta suave' }, 'Se resolvió sola: el valor volvió a la normalidad.') : null,
    !f.revision_por && !f.verificacion_por && !f.resuelta_auto ? el('span', { class: 'suave' }, 'Sin comentarios todavía') : null);
}
async function secSeguimiento(z) {
  const cuerpo = el('div', {});
  vaciar(z).append(cuerpo);
  async function cargar() {
    const d = await api.get('/seguimiento-alertas'), c = d.conteo;
    vaciar(cuerpo).append(
      el('p', { class: 'ayuda' }, 'Cada alerta pasa por: activa → reconocida (alguien va a revisar) → revisada (el encargado comenta) → resuelta (el supervisor o el administrador confirma). Si quien revisa es el supervisor o el administrador, su revisión es la definitiva y queda resuelta de inmediato. Si el valor vuelve a la normalidad antes de que alguien la atienda, se resuelve sola y queda en el historial.'),
      el('dl', { class: 'indicadores' }, [['Nadie ha ido a revisar', c.activa], ['En revisión por el encargado', c.reconocida], ['Falta confirmar (supervisor)', c.revisada], ['Resueltas en 24 h', c.resueltas_24h]]
        .map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, v)))),
      el('h3', {}, `Sin resolver (${d.pendientes.length})`),
      tabla([{ titulo: 'Situación', ver: (f) => [estado(f.estado), el('div', { class: 'meta suave' }, f.situacion.replace('PENDIENTE: ', ''))] },
        { titulo: 'Cultivo · dispositivo', ver: (f) => `${f.cultivo || '—'} · ${f.dispositivo || '—'}` }, { titulo: 'Detalle', campo: 'mensaje' },
        { titulo: 'Abierta hace', ver: (f) => minutosATexto(f.minutos_abierta) }, { titulo: 'Comentarios', ver: comentarios }], d.pendientes, 'No hay alertas sin resolver.'),
      el('h3', {}, 'Resueltas recientemente'),
      tabla([{ titulo: 'Resuelta', ver: (f) => fechaHora(f.resuelta_en && new Date(f.resuelta_en).toISOString()) }, { titulo: 'Cultivo · dispositivo', ver: (f) => `${f.cultivo || '—'} · ${f.dispositivo || '—'}` },
        { titulo: 'Detalle', campo: 'mensaje' }, { titulo: 'Tardó', ver: (f) => minutosATexto(f.minutos_abierta) }, { titulo: 'Comentarios', ver: comentarios }], d.resueltas, 'Aún no hay alertas resueltas.'));
  }
  await cargar();
  const t = setInterval(() => { if (!document.body.contains(z)) clearInterval(t); else if (!['TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) cargar().catch(() => {}); }, 8000);
}
async function vSeguimiento() { const z = contenido.firstChild; vaciar(z).append(encabezado('Seguimiento de alertas')); const c = el('div', {}); z.append(c); await secSeguimiento(c); }

/*  Informes (administrador)  */
const fechaLocal = (dias = 0) => { const d = new Date(Date.now() + dias * 86400000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const fmtMin = (v) => (v == null ? '—' : v < 1 ? '< 1 min' : v < 60 ? `${fmt(v, 0)} min` : `${Math.floor(v / 60)} h ${Math.round(v % 60)} min`);
const barraPct = (v) => (v == null ? el('span', { class: 'suave' }, '—') : el('span', { class: 'barra-pct', title: `${fmt(v, 1)} %` },
  el('span', { class: `relleno ${v >= 80 ? 'alto' : v >= 50 ? 'medio' : 'bajo'}`, style: { width: `${Math.max(2, Math.min(100, v))}%` } }), el('b', {}, `${fmt(v, 0)} %`)));
const CONTENIDOS = [['completo', 'Informe completo', 'Excel con todas las hojas: mediciones, alertas, eventos y trabajadores.'], ['mediciones', 'Mediciones', 'Historial de lo que midió cada sensor.'],
  ['alertas', 'Alertas y su seguimiento', 'Quién atendió cada alerta y qué comentó.'], ['eventos', 'Eventos', 'Cortes de comunicación, reconexiones y datos rechazados.'],
  ['resumen', 'Resumen por cultivo', 'Mínimo, promedio y máximo de cada sensor.'], ['trabajadores', 'Trabajadores', 'Qué hizo cada persona, su efectividad y las mantenciones.']];

async function secInformes(z) {
  const m = marco(z), [insts, cultivos, tipos] = await Promise.all([api.get('/instalaciones'), api.get('/cultivos'), api.get('/tipos-sensor')]);
  const f = { tipo: 'completo', formato: 'xlsx', desde: fechaLocal(-7), hasta: fechaLocal(0), instalacion_id: '', cultivo_id: '', tipo_variable: '', agregacion: '1h', estado: '' };
  const campo = (t, ent, ayuda) => el('label', { class: 'campo' }, el('span', {}, t), ent, ayuda ? el('small', { class: 'ayuda' }, ayuda) : null);
  const sel = (clave, opts, extra) => el('select', { onchange: (e) => { f[clave] = e.target.value; extra && extra(); } }, opts.map(([v, t]) => el('option', { value: v, selected: f[clave] === v }, t)));
  const selCultivo = el('select', { onchange: (e) => { f.cultivo_id = e.target.value; } });
  const pintarCultivos = () => { f.cultivo_id = ''; vaciar(selCultivo).append(el('option', { value: '' }, 'Todos'), cultivos.filter((c) => !f.instalacion_id || c.instalacion_id === f.instalacion_id).map((c) => el('option', { value: c.id }, `${c.nombre} (${nombreDe(insts, c.instalacion_id)})`))); };
  pintarCultivos();
  const inDesde = el('input', { type: 'date', value: f.desde, required: true, oninput: (e) => { f.desde = e.target.value; } });
  const inHasta = el('input', { type: 'date', value: f.hasta, required: true, oninput: (e) => { f.hasta = e.target.value; } });
  const rango = (d, h) => { f.desde = inDesde.value = d; f.hasta = inHasta.value = h; };
  const hoy = new Date(), pad = (n) => String(n).padStart(2, '0'), ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const rapidos = [['Hoy', () => rango(fechaLocal(0), fechaLocal(0))], ['7 días', () => rango(fechaLocal(-7), fechaLocal(0))], ['30 días', () => rango(fechaLocal(-30), fechaLocal(0))],
    ['Este mes', () => rango(ymd(new Date(hoy.getFullYear(), hoy.getMonth(), 1)), fechaLocal(0))], ['Mes anterior', () => rango(ymd(new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1)), ymd(new Date(hoy.getFullYear(), hoy.getMonth(), 0)))]];
  const tarjetas = el('div', { class: 'tiles', role: 'radiogroup', 'aria-label': 'Contenido del informe' });
  const zonaOpciones = el('div', { class: 'zona-op' }), previa = el('div', {});
  const pintarTarjetas = () => vaciar(tarjetas).append(CONTENIDOS.map(([v, t, d]) => el('button', { type: 'button', role: 'radio', 'aria-checked': String(f.tipo === v), class: `tile ${f.tipo === v ? 'sel' : ''}`,
    onclick: () => { f.tipo = v; pintarTarjetas(); pintarOpciones(); } }, el('strong', {}, t), el('span', {}, d))));
  const pintarOpciones = () => {
    const medidas = ['completo', 'mediciones', 'resumen'].includes(f.tipo), alertas = ['completo', 'alertas'].includes(f.tipo);
    vaciar(zonaOpciones).append(
      el('div', { class: 'card-informe filtros' }, el('h4', {}, '3 · Filtros'), el('div', { class: 'rejilla-campos' },
        campo('Instalación', sel('instalacion_id', [['', 'Todas'], ...insts.map((i) => [i.id, i.nombre])], pintarCultivos)), campo('Cultivo', selCultivo),
        medidas ? campo('Variable', sel('tipo_variable', [['', 'Todas'], ...tipos.map((t) => [t.tipo_variable, t.nombre])])) : null,
        alertas ? campo('Estado de las alertas', sel('estado', [['', 'Todas'], ['abiertas', 'Solo sin resolver'], ['resuelta', 'Solo resueltas'], ['activa', 'Activas'], ['reconocida', 'En revisión'], ['revisada', 'Falta confirmar']])) : null)),
      el('div', { class: 'card-informe formato' }, el('h4', {}, '4 · Formato'), el('div', { class: 'rejilla-campos' },
        campo('Archivo', sel('formato', [['xlsx', 'Excel (.xlsx)'], ['csv', 'CSV (para Excel en español)']]), f.tipo === 'completo' ? 'En CSV se entrega una sola tabla: el informe completo sale como mediciones.' : ''),
        medidas && f.tipo !== 'resumen' ? campo('Detalle de las mediciones', sel('agregacion', [['1h', 'Promedio por hora'], ['15m', 'Cada 15 minutos'], ['5m', 'Cada 5 minutos'], ['1d', 'Promedio por día'], ['crudo', 'Cada medición (sin agrupar)']]),
          'Si el rango es muy largo para ese detalle, el informe se agrupa solo (lo avisamos y queda anotado en el archivo): ya no falla.') : null)));
  };
  pintarTarjetas(); pintarOpciones();
  const boton = el('button', { class: 'boton', type: 'submit' }, 'Descargar informe');
  const verTrab = el('button', { class: 'boton-secundario', type: 'button' }, 'Ver desempeño en pantalla');
  const form = el('form', { class: 'informe' },
    el('p', { class: 'ayuda' }, 'Elige qué informe quieres, el período y los filtros. Las horas salen en hora de Santiago.'),
    el('div', { class: 'card-informe' }, el('h4', {}, '1 · Qué informe necesitas'), tarjetas),
    el('div', { class: 'card-informe periodo' }, el('h4', {}, '2 · Período'), el('div', { class: 'rejilla-campos' }, campo('Desde', inDesde), campo('Hasta (incluido)', inHasta)),
      el('div', { class: 'rapidos' }, rapidos.map(([t, fn]) => el('button', { type: 'button', class: 'boton-texto', onclick: fn }, t)))),
    zonaOpciones,
    el('div', { class: 'barra-descarga' }, el('span', { class: 'suave' }, 'Tus filtros se aplican a todas las hojas del archivo.'), el('span', { class: 'acciones' }, verTrab, boton)));
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); boton.disabled = true; boton.textContent = 'Generando…'; vaciar(m.msg);
    try {
      const r = await fetch(`/api/informes${qs(f)}`, { credentials: 'same-origin' });
      if (!r.ok) { const d = await r.json().catch(() => null); throw new ErrorApi((d && d.error && d.error.mensaje) || `Error ${r.status}`); }
      const blob = await r.blob(), mm = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || ''), nota = r.headers.get('X-Informe-Aviso');
      const a = el('a', { href: URL.createObjectURL(blob), download: mm ? mm[1] : `informe.${f.formato}` });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 15000);
      vaciar(m.msg).append(aviso('ok', `Informe descargado (${mm ? mm[1] : 'informe'}).`), nota ? aviso('warn', decodeURIComponent(nota)) : null);
    } catch (err) { m.mal(err); } finally { boton.disabled = false; boton.textContent = 'Descargar informe'; }
  });
  verTrab.addEventListener('click', async () => {
    verTrab.disabled = true; vaciar(previa).append(el('p', { class: 'suave' }, 'Calculando…'));
    try { vaciar(previa).append(tablaTrabajadores(await api.get(`/informes/trabajadores${qs(f)}`))); } catch (err) { vaciar(previa); m.mal(err); } finally { verTrab.disabled = false; }
  });
  m.cuerpo.append(form, previa);
}

/* Desempeño del personal en pantalla: atención, efectividad y mantenciones de cada persona. */
function tablaTrabajadores(d) {
  const cuestionadas = d.personas.reduce((s, p) => s + p.cuestionadas, 0);
  return el('section', { class: 'card-informe' },
    el('h4', {}, `Desempeño del personal · ${d.desde} al ${d.hasta}`),
    el('p', { class: 'ayuda' }, 'Atención = alertas de sus instalaciones que la persona reconoció. Efectividad = soluciones informadas que se confirmaron ÷ (esas + las que la medición o el supervisor cuestionaron). '
      + 'Con varios encargados en una instalación, la atención se reparte entre ellos.'),
    el('div', { class: 'indicadores compactos' },
      el('div', {}, el('dt', {}, 'Personas'), el('dd', {}, d.personas.length)), el('div', {}, el('dt', {}, 'Soluciones cuestionadas'), el('dd', {}, cuestionadas)),
      el('div', {}, el('dt', {}, 'Mantenciones derivadas'), el('dd', {}, d.mantenciones)), el('div', {}, el('dt', {}, 'Mantenciones abiertas'), el('dd', {}, d.mantenciones_abiertas))),
    tabla([{ titulo: 'Persona', ver: (p) => [el('strong', {}, p.nombre), el('div', { class: 'meta suave' }, p.rol_texto)] },
      { titulo: 'Atención', ver: (p) => (p.cumplimiento == null ? el('span', { class: 'suave' }, `${p.reconocidas} reconocidas`) : [barraPct(p.cumplimiento), el('div', { class: 'meta suave' }, `${p.reconocidas} de ${p.alertas_ambito}`)]) },
      { titulo: 'Respuesta media', ver: (p) => fmtMin(p.respuesta_min) },
      { titulo: 'Soluciones', num: true, campo: 'soluciones' }, { titulo: 'Efectivas', num: true, campo: 'efectivas' }, { titulo: 'Cuestionadas', num: true, ver: (p) => (p.cuestionadas ? el('strong', { class: 'alerta-n' }, p.cuestionadas) : 0) },
      { titulo: 'Efectividad', ver: (p) => barraPct(p.efectividad) }, { titulo: 'Resolución media', ver: (p) => fmtMin(p.resolucion_min) },
      { titulo: 'Sin atender', num: true, ver: (p) => (p.sin_atender == null ? '—' : p.sin_atender) }, { titulo: 'Derivaciones', num: true, campo: 'derivaciones' },
      { titulo: 'Mantenciones cerradas', num: true, campo: 'mantenciones_cerradas' }], d.personas, 'No hay personas activas.'));
}

/*  Equipos vinculados (administrador)  */
async function secEquipos(z) {
  const m = marco(z), datos = await api.get('/equipos'), recargar = () => secEquipos(z);
  m.cuerpo.append(el('p', { class: 'ayuda' }, 'Celulares y navegadores desde los que cada persona ingresó. Al revocar un equipo se cierran sus sesiones y no podrá volver a vincularse hasta que lo reactives.'),
    tabla([{ titulo: 'Persona', ver: (f) => [el('strong', {}, f.usuario || '—'), el('div', { class: 'meta suave' }, `${f.email || ''} · ${ROL[f.rol] || ''}`)] }, { titulo: 'Equipo', campo: 'nombre' },
      { titulo: 'Tipo', ver: (f) => (f.plataforma === 'android' ? 'App Android' : 'Navegador') }, { titulo: 'Vinculado', ver: (f) => fechaHora(f.creado_en) }, { titulo: 'Último uso', ver: (f) => fechaHora(f.ultimo_uso) },
      { titulo: 'Avisos push', ver: (f) => (f.con_push ? 'Sí' : 'No') }, { titulo: 'Estado', ver: (f) => (f.revocado ? 'Revocado' : 'Activo') },
      { titulo: '', ver: (f) => (f.revocado ? botonTexto('Reactivar', accion(m, () => api.post(`/equipos/${f.id}/reactivar`), 'Equipo reactivado.', recargar))
        : botonTexto('Revocar', () => confirm('¿Revocar este equipo? Se cerrará su sesión.') && accion(m, () => api.post(`/equipos/${f.id}/revocar`), 'Equipo revocado.', recargar)())) }], datos, 'Aún no hay equipos vinculados.'));
}

/*  Mantenciones y técnicos  */
async function secMantenciones(z) {
  const m = marco(z), recargar = () => secMantenciones(z);
  const filtro = z.dataset.filtroMant || 'abiertas', datos = await api.get(`/mantenciones${qs({ estado: filtro })}`);
  const sel = el('select', { onchange: (e) => { z.dataset.filtroMant = e.target.value; recargar(); } }, [['abiertas', 'Abiertas (pendientes y en curso)'], ['', 'Todas'], ['completada', 'Realizadas'], ['cancelada', 'Canceladas']]
    .map(([v, t]) => el('option', { value: v, selected: v === filtro }, t)));
  m.cuerpo.append(el('p', { class: 'ayuda' }, 'Órdenes de mantención creadas al derivar una alerta (visita de técnico, cambio de batería o de dispositivo…). Quien las atiende registra qué hizo; si el dispositivo vuelve a informar valores normales, la alerta se resuelve sola.'),
    el('label', { class: 'campo filtro-sensores' }, el('span', {}, 'Mostrar'), sel),
    datos.length ? el('div', { class: 'lista-mantenciones' }, datos.map((x) => el('article', { class: 'card-informe' },
      el('div', { class: 'meta suave' }, `${x.instalacion || ''} · ${x.cultivo || '—'} · ${x.dispositivo || '—'}`), cajaMantencion(x, recargar, 'boton-secundario'),
      el('a', { class: 'boton-texto', href: '#/alertas' }, 'Ver la alerta'))))
      : el('p', { class: 'ayuda' }, 'No hay mantenciones con este filtro.'));
}

const SECCIONES = [['usuarios', 'Usuarios', secUsuarios], ['instalaciones', 'Instalaciones', secInstalaciones], ['cultivos', 'Cultivos y plantas', secCultivos],
  ['dispositivos', 'Dispositivos y sensores', secDispositivos], ['tipos', 'Tipos de sensor', secTipos], ['reglas', 'Reglas de alerta', secReglas], ['seguimiento', 'Seguimiento de alertas', secSeguimiento], ['mantenciones', 'Mantenciones', secMantenciones], ['informes', 'Informes', secInformes],
  ['equipos', 'Equipos vinculados', secEquipos], ['parametros', 'Parámetros', secParametros], ['manual', 'Carga manual', secManual], ['auditoria', 'Auditoría', secAuditoria]];

/* Icono de la marca de cada sección (web/static/iconos). */
const ICONO_SECCION = { usuarios: 'usuarios', instalaciones: 'instalaciones', cultivos: 'instalaciones', dispositivos: 'dispositivos', tipos: 'parametros', reglas: 'reglas-alerta',
  seguimiento: 'bitacora', mantenciones: 'servicio-tecnico', alertas: 'tab-alertas', eventos: 'bitacora', informes: 'informes', equipos: 'equipos-vinculados', parametros: 'parametros',
  manual: 'estadisticas', auditoria: 'auditoria' };

/* Pantalla "Administración" de la web (pestañas). */
async function vAdministracion(clave) {
  const z = contenido.firstChild, cuerpo = el('div', {});
  vaciar(z).append(encabezado('Administración'), el('div', { class: 'pestanas', role: 'tablist' }, SECCIONES.map(([k, t]) => el('a', { href: `#/administracion/${k}`, role: 'tab', class: clave === k ? 'activa' : '' }, ICO(ICONO_SECCION[k], 'ico-pest'), t))), cuerpo);
  await (SECCIONES.find((s) => s[0] === clave) || SECCIONES[0])[2](cuerpo);
}
