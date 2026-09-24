// CSRF: cada pedido HTMX lleva el token de la sesión (modo con login propio)
document.addEventListener('htmx:configRequest', e => {
  const m = document.querySelector('meta[name=csrf-token]');
  if (m) e.detail.headers['X-CSRF-Token'] = m.content;
});
// Atajos de teclado (revisión): f favorita, p potencial, d descartar, c contactada, → / n siguiente, o abrir aviso
document.addEventListener('keydown', e => {
  if (e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
  const k = e.key === 'ArrowRight' ? 'n' : e.key.toLowerCase();
  const el = document.querySelector('[data-key="' + k + '"]');
  if (el) { e.preventDefault(); el.click(); }
});
// Galería de la tarjeta de Revisión: `paso` = -1 / +1 (con vuelta). Precarga la foto siguiente.
function galeria(el, paso) {
  const w = el.closest('.hero-w'), fotos = JSON.parse(w.dataset.fotos || '[]'), n = fotos.length;
  if (n < 2) return;
  const i = ((+w.dataset.i || 0) + paso + n) % n;
  w.dataset.i = i;
  w.querySelector('.hero').src = fotos[i];
  w.querySelectorAll('.h-dots i').forEach((d, k) => d.classList.toggle('on', k === i));
  w.querySelector('.h-fotos').textContent = `${i + 1}/${n}`;
  new Image().src = fotos[(i + 1) % n];
}
function precargarGaleria() {
  const w = document.querySelector('#card .hero-w[data-fotos]');
  if (w) new Image().src = JSON.parse(w.dataset.fotos)[1];
}
document.addEventListener('DOMContentLoaded', precargarGaleria);
document.addEventListener('htmx:afterSwap', e => { if (e.detail.target.id === 'card') precargarGaleria(); });

// Revisión en pantallas táctiles: deslizar la tarjeta ← descarta, → pasa a la siguiente (como el botón Saltar).
// Toque simple sobre la foto: costado izquierdo = foto anterior, resto = siguiente. Espera la ventana del doble toque
// (potencial) antes de cambiar la foto, así un doble toque nunca pasa fotos.
// El scroll vertical queda para el navegador (touch-action:pan-y en CSS); acá sólo se sigue el movimiento horizontal.
(() => {
  let art = null, x0 = 0, y0 = 0, dx = 0, eje = null, heroW = null, tapFoto = null;
  const umbral = () => Math.min(120, innerWidth * 0.28);
  const boton = dir => document.querySelector(dir < 0 ? '#card .card [data-key="d"]' : '#card .card [data-key="n"]');
  const mover = (d, anim) => {
    art.style.transition = anim ? 'transform .2s ease-out' : 'none';
    art.style.transform = d ? `translateX(${d}px) rotate(${d / 25}deg)` : '';
    art.dataset.swipe = d <= -umbral() ? 'izq' : d >= umbral() ? 'der' : '';
  };
  document.addEventListener('touchstart', e => {
    art = e.touches.length === 1 && !e.target.closest('textarea,button,a,input') && e.target.closest('#card article.card');
    if (!art) return;
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; eje = null;
    heroW = e.target.closest('.hero-w[data-fotos]');
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (!art) return;
    const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
    if (!eje && Math.hypot(mx, my) > 10) eje = Math.abs(mx) > Math.abs(my) ? 'x' : 'y';
    if (eje === 'x') { dx = mx; mover(dx, false); }
  }, { passive: true });
  let ultimoToque = 0, xt = 0, yt = 0;
  document.addEventListener('touchend', () => {
    if (art && !eje) {  // toque sin arrastrar: doble toque (como Instagram) = potencial
      const ahora = Date.now(), b = document.querySelector('#card .card [data-key="p"]');
      if (ahora - ultimoToque < 320 && Math.hypot(x0 - xt, y0 - yt) < 40 && b) {
        ultimoToque = 0;
        clearTimeout(tapFoto);   // era un doble toque: no cambiar de foto
        const pop = document.createElement('div');
        pop.className = 'pop'; pop.textContent = '◆';
        art.append(pop);
        if (!b.classList.contains('on')) setTimeout(() => b.click(), 450);  // como el like: nunca desmarca
      } else {
        ultimoToque = ahora; xt = x0; yt = y0;
        if (heroW) {
          const w = heroW, r = w.getBoundingClientRect(), paso = x0 - r.left < r.width * 0.35 ? -1 : 1;
          tapFoto = setTimeout(() => galeria(w, paso), 330);
        }
      }
    }
    if (!art || eje !== 'x') { art = null; return; }
    const dir = Math.sign(dx), b = boton(dir);
    if (Math.abs(dx) >= umbral() && b) {
      mover(dir * innerWidth * 1.2, true);
      setTimeout(() => b.click(), 180);  // htmx reemplaza la tarjeta por la siguiente
    } else {
      mover(0, true);
    }
    art = null;
  });
  document.addEventListener('touchcancel', () => { if (art) mover(0, true); art = null; });
  // Si el pedido falla, la tarjeta no se reemplaza: volverla a su lugar
  document.addEventListener('htmx:afterRequest', e => {
    const a = document.querySelector('#card article.card');
    if (!e.detail.successful && a) { a.style.transform = ''; a.dataset.swipe = ''; }
  });
})();
// Abrir el panel de vista rápida cuando llega su contenido
document.addEventListener('htmx:afterSwap', e => {
  if (e.detail.target.id === 'panel-body') document.getElementById('panel').showModal();
});
// Aviso embebido (puede ser bloqueado por el portal; se carga sólo a pedido)
function loadFrame(btn) {
  const f = document.createElement('iframe');
  f.className = 'emb'; f.src = btn.dataset.url; f.referrerPolicy = 'no-referrer';
  f.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups');
  btn.replaceWith(f);
}
// Comparar: selección en la lista
const sel = new Set(JSON.parse(sessionStorage.getItem('cmp') || '[]'));
function cmpBar() {
  const bar = document.getElementById('cmpbar'); if (!bar) return;
  bar.style.display = sel.size ? 'block' : 'none';
  bar.querySelector('a').href = '/comparar?ids=' + [...sel].join(',');
  bar.querySelector('span').textContent = sel.size;
  document.querySelectorAll('.cmp').forEach(c => c.checked = sel.has(c.value));
}
document.addEventListener('change', e => {
  if (!e.target.classList.contains('cmp')) return;
  e.target.checked ? sel.add(e.target.value) : sel.delete(e.target.value);
  sessionStorage.setItem('cmp', JSON.stringify([...sel])); cmpBar();
});
document.addEventListener('htmx:afterSwap', cmpBar); document.addEventListener('DOMContentLoaded', cmpBar);

// Filtros: abiertos por defecto en pantallas anchas
document.addEventListener('DOMContentLoaded', () => {
  const d = document.getElementById('fdet');
  if (d && matchMedia('(min-width:701px)').matches) d.open = true;
});
// Tema: automático (sigue al dispositivo) → claro → oscuro. Se guarda en este dispositivo; el <head> lo aplica antes
// de pintar la página (ver base.html) y el CSS lo lee de <html data-tema>.
const TEMAS = ['auto', 'claro', 'oscuro'];
const TEMA_NOMBRE = { auto: 'automático (del dispositivo)', claro: 'claro', oscuro: 'oscuro' };
const temaActual = () => document.documentElement.dataset.tema || 'auto';
function pintarTema() {
  const b = document.getElementById('tema');
  if (!b) return;
  const t = temaActual(), sig = TEMAS[(TEMAS.indexOf(t) + 1) % TEMAS.length];
  b.querySelector('use').setAttribute('href', '#i-tema-' + t);
  b.title = `Tema ${TEMA_NOMBRE[t]} · tocá para pasar a ${TEMA_NOMBRE[sig]}`;
  b.setAttribute('aria-label', b.title);
}
function cambiarTema() {
  const t = TEMAS[(TEMAS.indexOf(temaActual()) + 1) % TEMAS.length];
  if (t === 'auto') delete document.documentElement.dataset.tema;
  else document.documentElement.dataset.tema = t;
  try { t === 'auto' ? localStorage.removeItem('inmo-tema') : localStorage.setItem('inmo-tema', t); } catch { /* sin almacenamiento: vale para esta página */ }
  pintarTema();
}
document.addEventListener('DOMContentLoaded', () => {
  pintarTema();
  document.getElementById('tema')?.addEventListener('click', cambiarTema);
});
// Mini mapa de la tarjeta de Revisión con la ubicación del aviso. Estático: no se arrastra ni hace zoom (los gestos
// de la tarjeta, deslizar y doble toque, siguen andando encima). Se arma de nuevo cada vez que htmx trae otra tarjeta.
function miniMapa() {
  const el = document.querySelector('#card .mini-mapa[data-lat]');
  if (!el || !window.L || el.dataset.listo) return;
  el.dataset.listo = '1';
  const ll = [+el.dataset.lat, +el.dataset.lng];
  const m = L.map(el, { zoomControl: false, dragging: false, touchZoom: false, scrollWheelZoom: false, doubleClickZoom: false,
                        boxZoom: false, keyboard: false, attributionControl: true }).setView(ll, 15);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, referrerPolicy: 'origin', attribution: '© OpenStreetMap' }).addTo(m);
  const acc = getComputedStyle(document.documentElement).getPropertyValue('--acc').trim() || '#1b6b58';
  L.circleMarker(ll, { radius: 9, color: '#fff', weight: 3, fillColor: acc, fillOpacity: 1, interactive: false }).addTo(m);
}
document.addEventListener('DOMContentLoaded', miniMapa);
document.addEventListener('htmx:afterSwap', e => { if (e.detail.target.id === 'card') miniMapa(); });
