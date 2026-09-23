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
