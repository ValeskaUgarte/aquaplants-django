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

/* ---------------- Usuarios ---------------- */
async function secUsuarios(z) {
  const m = marco(z), [usuarios, insts] = await Promise.all([api.get('/usuarios'), api.get('/instalaciones')]);
  const roles = Object.entries(ROL).map(([valor, texto]) => ({ valor, texto }));
  const campoInst = { nombre: 'instalaciones', etiqueta: 'Instalaciones asignadas (Encargado de cultivo)', tipo: 'multi', opciones: opciones(insts, 'id', 'nombre') };
  const recargar = () => secUsuarios(z);
  const nuevo = () => m.mostrarForm(formulario({ textoBoton: 'Crear usuario', onCancelar: m.cerrarForm, campos: [
    { nombre: 'email', etiqueta: 'Correo', tipo: 'email', requerido: true }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true },
    { nombre: 'rol', etiqueta: 'Rol', tipo: 'select', opciones: roles, requerido: true },
    { nombre: 'password', etiqueta: 'Contraseña inicial', tipo: 'password', requerido: true, ayuda: 'Mínimo 12 caracteres con mayúscula, minúscula, número y símbolo.' }, campoInst],
    onEnviar: async (d) => { await api.post('/usuarios', d); await recargar(); } }));
  const editar = (u) => m.mostrarForm(formulario({ textoBoton: 'Guardar cambios', onCancelar: m.cerrarForm,
    inicial: { nombre: u.nombre, rol: u.rol, activo: u.activo, instalaciones: u.instalaciones }, campos: [
      { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text' }, { nombre: 'rol', etiqueta: 'Rol', tipo: 'select', opciones: roles }, { nombre: 'activo', etiqueta: 'Cuenta activa', tipo: 'checkbox' },
      { nombre: 'password', etiqueta: 'Nueva contraseña (opcional)', tipo: 'password', ayuda: 'Al cambiarla se cierran las sesiones del usuario.' }, campoInst],
    onEnviar: async (d) => { await api.patch(`/usuarios/${u.id}`, d); await recargar(); } }));
  m.cuerpo.append(el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nuevo }, 'Nuevo usuario')),
    tabla([{ titulo: 'Nombre', campo: 'nombre' }, { titulo: 'Correo', campo: 'email' }, { titulo: 'Rol', ver: (f) => ROL[f.rol] }, { titulo: 'Estado', ver: (f) => (f.activo ? 'Activo' : 'Desactivado') },
      { titulo: '2FA', ver: (f) => (f.totp_activo ? 'Sí' : 'No') }, { titulo: 'Instalaciones', ver: (f) => (f.rol === 'encargado_cultivo' ? f.instalaciones.map((i) => nombreDe(insts, i)).join(', ') || 'ninguna' : 'todas') },
      { titulo: 'Último ingreso', ver: (f) => fechaHora(f.ultimo_login) }, { titulo: '', ver: (f) => botonTexto('Editar', () => editar(f)) }], usuarios));
}

/* ---------------- Instalaciones ---------------- */
async function secInstalaciones(z) {
  const m = marco(z), datos = await api.get('/instalaciones'), recargar = () => secInstalaciones(z);
  const nueva = () => m.mostrarForm(formulario({ textoBoton: 'Crear instalación', onCancelar: m.cerrarForm, campos: [
    { nombre: 'codigo', etiqueta: 'Código (id_sistema)', tipo: 'text', requerido: true, ayuda: 'Letras, números, guion. Identifica a la instalación.' },
    { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true }, { nombre: 'ubicacion', etiqueta: 'Ubicación', tipo: 'text' },
    { nombre: 'descripcion', etiqueta: 'Descripción', tipo: 'text' }],
    onEnviar: async (d) => { await api.post('/instalaciones', d); await recargar(); } }));
  const editar = (i) => m.mostrarForm(formulario({ textoBoton: 'Guardar cambios', onCancelar: m.cerrarForm, inicial: i, campos: [
    { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text' }, { nombre: 'ubicacion', etiqueta: 'Ubicación', tipo: 'text' }, { nombre: 'descripcion', etiqueta: 'Descripción', tipo: 'text' }, { nombre: 'activa', etiqueta: 'Instalación activa', tipo: 'checkbox' }],
    onEnviar: async (d) => { await api.patch(`/instalaciones/${i.id}`, d); await recargar(); } }));
  m.cuerpo.append(el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nueva }, 'Nueva instalación')),
    tabla([{ titulo: 'Código', campo: 'codigo' }, { titulo: 'Nombre', campo: 'nombre' }, { titulo: 'Ubicación', ver: (f) => f.ubicacion || '—' }, { titulo: 'Descripción', ver: (f) => f.descripcion || '—' },
      { titulo: 'Estado', ver: (f) => (f.activa ? 'Activa' : 'Inactiva') }, { titulo: '', ver: (f) => botonTexto('Editar', () => editar(f)) }], datos));
}

/* ---------------- Cultivos y plantas ---------------- */
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

/* ---------------- Dispositivos y sensores ---------------- */
async function secDispositivos(z) {
  const m = marco(z), [insts, disp, sens, tipos, cultivos] = await Promise.all([api.get('/instalaciones'), api.get('/dispositivos'), api.get('/sensores'), api.get('/tipos-sensor'), api.get('/cultivos')]);
  const recargar = () => secDispositivos(z), secretoZona = el('div', {});
  const mostrarSecreto = (s) => vaciar(secretoZona).append(el('div', { class: 'aviso aviso-warn', role: 'alert' }, el('strong', {}, 'Secreto del dispositivo (se muestra una sola vez): '), el('code', { class: 'secreto' }, s), ' ',
    botonTexto('Ya lo guardé', () => vaciar(secretoZona))));
  const libres = cultivos.filter((c) => !disp.some((d) => d.cultivo_id === c.id));
  const nuevoD = () => m.mostrarForm(formulario({ textoBoton: 'Crear dispositivo', onCancelar: m.cerrarForm, campos: [
    { nombre: 'cultivo_id', etiqueta: 'Cultivo que monitorea (solo cultivos sin dispositivo)', tipo: 'select', requerido: true, opciones: libres.map((c) => ({ valor: c.id, texto: `${c.nombre} — ${nombreDe(insts, c.instalacion_id)}` })) },
    { nombre: 'codigo', etiqueta: 'Código (id_dispositivo)', tipo: 'text', requerido: true }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true }],
    onEnviar: async (d) => { const c = cultivos.find((x) => x.id === d.cultivo_id); const r = await api.post('/dispositivos', { ...d, instalacion_id: c.instalacion_id }); await recargar(); mostrarSecreto(r.secreto); } }));
  const nuevoS = () => m.mostrarForm(formulario({ textoBoton: 'Crear sensor', onCancelar: m.cerrarForm, campos: [
    { nombre: 'dispositivo_id', etiqueta: 'Dispositivo (el sensor mide el cultivo de su dispositivo)', tipo: 'select', requerido: true, opciones: disp.map((d) => ({ valor: d.id, texto: `${d.codigo} — ${d.cultivo_nombre || 'sin cultivo'}` })) },
    { nombre: 'tipo_sensor_id', etiqueta: 'Tipo de sensor', tipo: 'select', requerido: true, opciones: tipos.map((t) => ({ valor: t.id, texto: `${t.nombre} (${t.unidad})` })) },
    { nombre: 'codigo', etiqueta: 'Código (id_sensor)', tipo: 'text', requerido: true }, { nombre: 'nombre', etiqueta: 'Nombre', tipo: 'text', requerido: true }],
    onEnviar: async (d) => { await api.post('/sensores', d); await recargar(); } }));
  m.cuerpo.append(secretoZona, el('p', { class: 'ayuda' }, 'Regla del proyecto: un dispositivo por cultivo. Los sensores de un dispositivo miden el cultivo al que pertenece.'),
    el('div', { class: 'acciones' }, el('button', { class: 'boton', type: 'button', onclick: nuevoD, disabled: !libres.length, title: libres.length ? '' : 'Todos los cultivos ya tienen dispositivo' }, 'Nuevo dispositivo'),
      el('button', { class: 'boton-secundario', type: 'button', onclick: nuevoS, disabled: !disp.length }, 'Nuevo sensor')),
    el('h3', {}, 'Dispositivos'),
    tabla([{ titulo: 'Cultivo', ver: (f) => el('strong', {}, f.cultivo_nombre || '—') }, { titulo: 'Dispositivo', ver: (f) => `${f.codigo} · ${f.nombre}` }, { titulo: 'Instalación', ver: (f) => nombreDe(insts, f.instalacion_id) },
      { titulo: 'Conexión', ver: (f) => estado(f.estado_conexion) }, { titulo: 'Última comunicación', ver: (f) => fechaHora(f.ultima_comunicacion) }, { titulo: 'Activo', ver: (f) => (f.activo ? 'Sí' : 'No') },
      { titulo: 'Acciones', ver: (f) => el('span', { class: 'acciones' }, botonTexto(f.activo ? 'Desactivar' : 'Activar', accion(m, () => api.patch(`/dispositivos/${f.id}`, { activo: !f.activo }), 'Dispositivo actualizado.', recargar)),
        botonTexto('Rotar secreto', async () => { if (confirm('El secreto anterior dejará de funcionar. ¿Continuar?')) { try { const r = await api.post(`/dispositivos/${f.id}/rotar-secreto`); mostrarSecreto(r.secreto); } catch (e) { m.mal(e); } } })) }], disp),
    el('h3', {}, 'Sensores'),
    tabla([{ titulo: 'Cultivo', ver: (f) => f.cultivo_nombre || '—' }, { titulo: 'Dispositivo', campo: 'dispositivo_codigo' }, { titulo: 'Sensor', ver: (f) => `${f.codigo} · ${f.nombre}` }, { titulo: 'Variable', campo: 'tipo_nombre' },
      { titulo: 'Unidad', campo: 'unidad' }, { titulo: 'Activo', ver: (f) => (f.activo ? 'Sí' : 'No') },
      { titulo: '', ver: (f) => botonTexto(f.activo ? 'Desactivar' : 'Activar', accion(m, () => api.patch(`/sensores/${f.id}`, { activo: !f.activo }), 'Sensor actualizado.', recargar)) }], sens));
}

/* ---------------- Tipos de sensor ---------------- */
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

/* ---------------- Reglas de alerta ---------------- */
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

/* ---------------- Parámetros del sistema ---------------- */
async function secParametros(z) {
  const m = marco(z), datos = await api.get('/parametros'), recargar = () => secParametros(z);
  datos.forEach((p) => {
    const entrada = el('input', { type: 'number', min: 0, value: p.valor, required: true });
    const f = el('form', { class: 'panel-form' }, el('label', { class: 'campo' }, el('span', {}, p.clave.replaceAll('_', ' ')), entrada, el('small', { class: 'ayuda' }, p.descripcion)), el('button', { class: 'boton', type: 'submit' }, 'Guardar'));
    f.addEventListener('submit', (e) => { e.preventDefault(); accion(m, () => api.put(`/parametros/${p.clave}`, { valor: String(entrada.value) }), 'Parámetro guardado.', recargar)(); });
    m.cuerpo.append(f);
  });
}

/* ---------------- Carga manual ---------------- */
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

/* ---------------- Auditoría ---------------- */
async function secAuditoria(z) {
  const m = marco(z), datos = await api.get('/auditoria'), recargar = () => secAuditoria(z), verZona = el('div', {});
  m.cuerpo.append(el('div', { class: 'acciones' }, el('button', { class: 'boton-secundario', type: 'button', onclick: recargar }, 'Actualizar'),
    el('button', { class: 'boton', type: 'button', onclick: async () => { const v = await api.get('/auditoria/verificar'); vaciar(verZona).append(aviso(v.ok ? 'ok' : 'error', v.ok ? `Registro íntegro (${v.filas} entradas encadenadas).` : `Se detectó una alteración en la entrada ${v.filaCorrupta}.`)); } }, 'Verificar integridad del registro')),
    verZona, tabla([{ titulo: 'Fecha', ver: (f) => fechaHora(f.ocurrido_en) }, { titulo: 'Usuario', ver: (f) => f.usuario || '—' }, { titulo: 'Acción', campo: 'accion' }, { titulo: 'Entidad', ver: (f) => f.entidad || '—' }, { titulo: 'IP', ver: (f) => f.ip || '—' }], datos));
}

/* ---------------- Solo lectura (consola /admin) ---------------- */
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


/* ---------------- Seguimiento de alertas (administrador y supervisor) ---------------- */
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

/* ---------------- Informes (administrador) ---------------- */
const fechaLocal = (dias = 0) => { const d = new Date(Date.now() + dias * 86400000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
async function secInformes(z) {
  const m = marco(z), [insts, cultivos, tipos] = await Promise.all([api.get('/instalaciones'), api.get('/cultivos'), api.get('/tipos-sensor')]);
  const f = { tipo: 'completo', formato: 'xlsx', desde: fechaLocal(-7), hasta: fechaLocal(0), instalacion_id: '', cultivo_id: '', tipo_variable: '', agregacion: '1h', estado: '' };
  const sel = (clave, opts, extra) => el('select', { onchange: (e) => { f[clave] = e.target.value; extra && extra(); } }, opts.map(([v, t]) => el('option', { value: v, selected: f[clave] === v }, t)));
  const selCultivo = el('select', { onchange: (e) => { f.cultivo_id = e.target.value; } });
  const pintarCultivos = () => { f.cultivo_id = ''; vaciar(selCultivo).append(el('option', { value: '' }, 'Todos'), cultivos.filter((c) => !f.instalacion_id || c.instalacion_id === f.instalacion_id).map((c) => el('option', { value: c.id }, `${c.nombre} (${nombreDe(insts, c.instalacion_id)})`))); };
  pintarCultivos();
  const campo = (t, ent, ayuda) => el('label', { class: 'campo' }, el('span', {}, t), ent, ayuda ? el('small', { class: 'ayuda' }, ayuda) : null);
  const boton = el('button', { class: 'boton', type: 'submit' }, 'Descargar informe');
  const form = el('form', { class: 'panel-form' },
    el('p', { class: 'ayuda' }, 'Genera un archivo con el historial por fecha, instalación, cultivo y sensor, y con el seguimiento de las alertas (quién las atendió y qué comentó). Las horas salen en hora de Santiago.'),
    el('div', { class: 'rejilla-form' },
      campo('Contenido', sel('tipo', [['completo', 'Informe completo (Excel con todas las hojas)'], ['mediciones', 'Solo mediciones (historial)'], ['alertas', 'Solo alertas y su seguimiento'], ['eventos', 'Solo eventos'], ['resumen', 'Solo resumen por cultivo']])),
      campo('Formato', sel('formato', [['xlsx', 'Excel (.xlsx)'], ['csv', 'CSV (para Excel en español)']]), 'En CSV se descarga una sola tabla: el informe completo se entrega como mediciones.'),
      campo('Desde', el('input', { type: 'date', value: f.desde, required: true, oninput: (e) => { f.desde = e.target.value; } })),
      campo('Hasta (incluido)', el('input', { type: 'date', value: f.hasta, required: true, oninput: (e) => { f.hasta = e.target.value; } })),
      campo('Instalación', sel('instalacion_id', [['', 'Todas'], ...insts.map((i) => [i.id, i.nombre])], pintarCultivos)),
      campo('Cultivo', selCultivo),
      campo('Variable', sel('tipo_variable', [['', 'Todas'], ...tipos.map((t) => [t.tipo_variable, t.nombre])])),
      campo('Mediciones agrupadas', sel('agregacion', [['1h', 'Promedio por hora'], ['15m', 'Cada 15 minutos'], ['5m', 'Cada 5 minutos'], ['1d', 'Promedio por día'], ['crudo', 'Sin agrupar (cada medición)']]), 'Sin agrupar genera muchas filas: úsalo con rangos cortos.'),
      campo('Estado de las alertas', sel('estado', [['', 'Todas'], ['abiertas', 'Solo sin resolver'], ['resuelta', 'Solo resueltas'], ['activa', 'Activas'], ['reconocida', 'En revisión'], ['revisada', 'Falta confirmar']]))),
    el('div', { class: 'acciones' }, boton));
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); boton.disabled = true; boton.textContent = 'Generando…'; vaciar(m.msg);
    try {
      const r = await fetch(`/api/informes${qs(f)}`, { credentials: 'same-origin' });
      if (!r.ok) { const d = await r.json().catch(() => null); throw new ErrorApi((d && d.error && d.error.mensaje) || `Error ${r.status}`); }
      const blob = await r.blob(), mm = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '');
      const a = el('a', { href: URL.createObjectURL(blob), download: mm ? mm[1] : `informe.${f.formato}` });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 15000);
      m.ok(`Informe descargado (${mm ? mm[1] : 'informe'}).`);
    } catch (err) { m.mal(err); } finally { boton.disabled = false; boton.textContent = 'Descargar informe'; }
  });
  m.cuerpo.append(form);
}

/* ---------------- Equipos vinculados (administrador) ---------------- */
async function secEquipos(z) {
  const m = marco(z), datos = await api.get('/equipos'), recargar = () => secEquipos(z);
  m.cuerpo.append(el('p', { class: 'ayuda' }, 'Celulares y navegadores desde los que cada persona ingresó. Al revocar un equipo se cierran sus sesiones y no podrá volver a vincularse hasta que lo reactives.'),
    tabla([{ titulo: 'Persona', ver: (f) => [el('strong', {}, f.usuario || '—'), el('div', { class: 'meta suave' }, `${f.email || ''} · ${ROL[f.rol] || ''}`)] }, { titulo: 'Equipo', campo: 'nombre' },
      { titulo: 'Tipo', ver: (f) => (f.plataforma === 'android' ? 'App Android' : 'Navegador') }, { titulo: 'Vinculado', ver: (f) => fechaHora(f.creado_en) }, { titulo: 'Último uso', ver: (f) => fechaHora(f.ultimo_uso) },
      { titulo: 'Avisos push', ver: (f) => (f.con_push ? 'Sí' : 'No') }, { titulo: 'Estado', ver: (f) => (f.revocado ? 'Revocado' : 'Activo') },
      { titulo: '', ver: (f) => (f.revocado ? botonTexto('Reactivar', accion(m, () => api.post(`/equipos/${f.id}/reactivar`), 'Equipo reactivado.', recargar))
        : botonTexto('Revocar', () => confirm('¿Revocar este equipo? Se cerrará su sesión.') && accion(m, () => api.post(`/equipos/${f.id}/revocar`), 'Equipo revocado.', recargar)())) }], datos, 'Aún no hay equipos vinculados.'));
}

const SECCIONES = [['usuarios', 'Usuarios', secUsuarios], ['instalaciones', 'Instalaciones', secInstalaciones], ['cultivos', 'Cultivos y plantas', secCultivos],
  ['dispositivos', 'Dispositivos y sensores', secDispositivos], ['tipos', 'Tipos de sensor', secTipos], ['reglas', 'Reglas de alerta', secReglas], ['seguimiento', 'Seguimiento de alertas', secSeguimiento], ['informes', 'Informes', secInformes],
  ['equipos', 'Equipos vinculados', secEquipos], ['parametros', 'Parámetros', secParametros], ['manual', 'Carga manual', secManual], ['auditoria', 'Auditoría', secAuditoria]];

/* Pantalla «Administración» de la web (pestañas). */
async function vAdministracion(clave) {
  const z = contenido.firstChild, cuerpo = el('div', {});
  vaciar(z).append(encabezado('Administración'), el('div', { class: 'pestanas', role: 'tablist' }, SECCIONES.map(([k, t]) => el('a', { href: `#/administracion/${k}`, role: 'tab', class: clave === k ? 'activa' : '' }, t))), cuerpo);
  await (SECCIONES.find((s) => s[0] === clave) || SECCIONES[0])[2](cuerpo);
}
