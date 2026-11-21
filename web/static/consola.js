/* Consola de administración en /admin (equivalente al sitio de administración de Django). Solo para el rol Administrador. */
const raiz = document.getElementById('app');
const GRUPOS = [['Gestión de usuarios', ['usuarios']], ['Estructura de cultivos', ['instalaciones', 'cultivos', 'dispositivos', 'tipos']],
  ['Alertas y reglas', ['reglas', 'seguimiento', 'mantenciones', 'alertas', 'eventos']], ['Informes', ['informes']], ['Sistema', ['equipos', 'parametros', 'manual', 'auditoria']]];
const TODAS = [...SECCIONES, ['alertas', 'Alertas (consulta)', secAlertasTodas], ['eventos', 'Eventos (consulta)', secEventosTodos]];
let actual = 'usuarios', zona;

function pintarMenu(nav) {
  vaciar(nav);
  for (const [grupo, claves] of GRUPOS) nav.append(el('div', { class: 'modulo' }, el('h3', {}, grupo),
    claves.map((k) => el('a', { href: `#${k}`, class: k === actual ? 'activo' : '' }, ICO(ICONO_SECCION[k], 'ico-menu'), TODAS.find((s) => s[0] === k)[1]))));
}
async function mostrar(nav) {
  actual = (location.hash || '#usuarios').slice(1);
  if (!TODAS.some((s) => s[0] === actual)) actual = 'usuarios';
  pintarMenu(nav);
  const s = TODAS.find((x) => x[0] === actual);
  vaciar(zona).append(el('h2', {}, s[1]));
  const c = el('div', {}); zona.append(c);
  try { await s[2](c); } catch (e) { vaciar(c).append(aviso('error', mensajeError(e))); }
}
function armar() {
  vaciar(raiz);
  if (AQ.usuario.rol !== 'administrador') {
    raiz.append(el('div', { class: 'login-caja' }, el('div', { class: 'login' }, el('h1', {}, 'Acceso restringido'), el('p', {}, 'Esta consola es solo para el rol Administrador.'),
      el('a', { class: 'boton', href: '/' }, 'Volver al sistema'), el('button', { class: 'boton-secundario', type: 'button', onclick: cerrarSesion }, 'Cambiar de usuario'))));
    return;
  }
  const nav = el('nav', { class: 'menu-consola', 'aria-label': 'Módulos' });
  zona = el('main', { class: 'zona-consola' });
  raiz.append(el('header', { class: 'cab-consola' }, logo('logo-consola'), el('h1', {}, 'Administración de AquaPlants'), el('span', { class: 'espacio' }),
    el('span', {}, `Bienvenido, ${AQ.usuario.nombre}. `), el('a', { href: '/' }, 'Ver el sitio'), ' / ', el('button', { type: 'button', class: 'boton-texto claro', onclick: cerrarSesion }, 'Cerrar sesión')),
    el('div', { class: 'cuerpo-consola' }, nav, zona));
  window.addEventListener('hashchange', () => mostrar(nav));
  mostrar(nav);
}
iniciarSesion(raiz, 'Administración de AquaPlants', armar);
