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
// Revisión en pantallas táctiles: deslizar la tarjeta ← descarta, → pasa a la siguiente (como el botón Saltar).
// El scroll vertical queda para el navegador (touch-action:pan-y en CSS); acá sólo se sigue el movimiento horizontal.
(() => {
  let art = null, x0 = 0, y0 = 0, dx = 0, eje = null;
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
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (!art) return;
    const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
    if (!eje && Math.hypot(mx, my) > 10) eje = Math.abs(mx) > Math.abs(my) ? 'x' : 'y';
    if (eje === 'x') { dx = mx; mover(dx, false); }
  }, { passive: true });
  document.addEventListener('touchend', () => {
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
