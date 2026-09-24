// Corre al empezar a cargar cada página de Zonaprop (document_start). La página trae los datos de los avisos (todas
// las fotos, coordenadas) en <script id="preloadedData">, pero lo borra del DOM después de usarlo; acá se guarda su
// texto antes, para que background.js lo agregue al HTML que se manda a Inmo (el parser lo necesita).
// Comparte el «mundo aislado» de la extensión con chrome.scripting.executeScript, que lee `window.__inmoDatos`.
(() => {
  window.__inmoDatos = "";
  const guardar = el => {
    if (el.id === "preloadedData" && el.textContent && !window.__inmoDatos) window.__inmoDatos = el.textContent;
  };
  const obs = new MutationObserver(cambios => {
    for (const c of cambios) for (const n of c.addedNodes) if (n.nodeType === 1 && n.tagName === "SCRIPT") guardar(n);
    const el = document.getElementById("preloadedData");     // por si el texto llegó después que el nodo
    if (el) guardar(el);
  });
  obs.observe(document, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", () => {
    const el = document.getElementById("preloadedData");
    if (el) guardar(el);
    obs.disconnect();
  });
})();
