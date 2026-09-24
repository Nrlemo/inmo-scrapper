// Inmo · ronda por navegador.
//
// Una vez por día abre las búsquedas de Zonaprop en una ventana minimizada de este navegador, página por página, y le
// manda cada una al servidor de Inmo (/api/navegador/*). El servidor decide qué página sigue y cuánto esperar (mismas
// zonas, topes y pausas que el scrapper), guarda los avisos y, al terminar una ronda completa, da de baja los que ya no
// aparecen. Acá no hay lógica de búsqueda: sólo abrir, esperar, leer y enviar.
//
// El service worker de una extensión MV3 se apaga cuando está inactivo, así que la ronda es una máquina de estados
// guardada en chrome.storage.local ("ronda") y los pasos se disparan con chrome.alarms o con eventos de pestañas.

const MIN_PAUSA_S = 30;           // chrome.alarms no admite menos de 30 s
const TIMEOUT_CARGA_MIN = 2;      // si la página no carga en este tiempo, se informa como error y se sigue
const DESAFIO = /un momento|just a moment|attention required|verific/i;   // título de la página de Cloudflare

// ---------- configuración y estado ----------
const config = () => chrome.storage.local.get({ servidor: "", token: "", hora: "03:30", activa: true });
const ronda = async () => (await chrome.storage.local.get({ ronda: null })).ronda;
const guardarRonda = r => chrome.storage.local.set({ ronda: r });
const anotar = (estado, mensaje) =>
  chrome.storage.local.set({ ultima: { fecha: new Date().toISOString(), estado, mensaje: String(mensaje || "") } });
const dormir = ms => new Promise(ok => setTimeout(ok, ms));

async function api(ruta, cuerpo) {
  const { servidor, token } = await config();
  if (!servidor || !token) throw new Error("Falta configurar el servidor y el token (opciones de la extensión)");
  const r = await fetch(servidor.replace(/\/+$/, "") + ruta, {
    method: cuerpo === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
  });
  if (!r.ok) throw new Error(`${ruta}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

// ---------- programación diaria ----------
function proxima(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  if (d <= new Date()) d.setDate(d.getDate() + 1);
  return d.getTime() + Math.random() * 20 * 60 * 1000;   // demora aleatoria de hasta 20 min, distinta cada día
}

async function programar() {
  const c = await config();
  await chrome.alarms.clear("diaria");
  if (c.activa && c.servidor && c.token) chrome.alarms.create("diaria", { when: proxima(c.hora) });
}

// Si a la hora programada el navegador estaba cerrado, la ronda del día se corre al abrirlo (el servidor igual la
// omite si hubo otra hace menos del intervalo mínimo). Si quedó una ronda a medias, se retoma donde estaba.
async function ponerseAlDia() {
  const c = await config();
  if (await ronda()) return chrome.alarms.create("paso", { delayInMinutes: 1 });
  if (!(c.activa && c.servidor && c.token)) return;
  const { ultimaRonda } = await chrome.storage.local.get({ ultimaRonda: 0 });
  const [h, m] = c.hora.split(":").map(Number);
  const hoy = new Date();
  hoy.setHours(h, m, 0, 0);
  if (Date.now() > hoy.getTime() && ultimaRonda < hoy.getTime()) chrome.alarms.create("atrasada", { delayInMinutes: 3 });
}

chrome.runtime.onInstalled.addListener(() => { programar(); ponerseAlDia(); });
chrome.runtime.onStartup.addListener(() => { programar(); ponerseAlDia(); });
chrome.storage.onChanged.addListener(cambios => {
  if (["servidor", "token", "hora", "activa"].some(k => k in cambios)) programar();
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

chrome.alarms.onAlarm.addListener(async a => {
  if (a.name === "diaria") { await programar(); await iniciarRonda(); }
  else if (a.name === "atrasada") await iniciarRonda();
  else if (a.name === "paso") await abrir();
  else if (a.name === "timeout") await cargaFallida("la página no terminó de cargar");
});

chrome.runtime.onMessage.addListener((msg, _sender, responder) => {
  if (msg === "correr") iniciarRonda().then(() => responder(true));
  else if (msg === "cancelar") cancelar().then(() => responder(true));
  return true;
});

// ---------- la ronda ----------
async function iniciarRonda() {
  if (await ronda()) return;                                   // ya hay una en curso
  await chrome.storage.local.set({ ultimaRonda: Date.now() });
  let r;
  try { r = await api("/api/navegador/ronda", {}); }
  catch (e) { return anotar("error", e.message); }
  if (r.omitir) return anotar("omitida", r.omitir);
  await guardarRonda({ id: r.ronda, url: r.url, tabId: null, windowId: null, cargando: false });
  await anotar("corriendo", `Ronda ${r.ronda} en curso`);
  await abrir();
}

async function existeTab(id) {
  try { await chrome.tabs.get(id); return true; } catch { return false; }
}

async function abrir() {
  const r = await ronda();
  if (!r) return;
  try {
    if (r.tabId != null && await existeTab(r.tabId)) {
      await chrome.tabs.update(r.tabId, { url: r.url });
    } else {
      try {
        const w = await chrome.windows.create({ url: r.url, focused: false, state: "minimized" });
        r.windowId = w.id;
        r.tabId = w.tabs[0].id;
      } catch {                                                   // sin ventanas minimizadas: pestaña de fondo
        r.windowId = null;
        r.tabId = (await chrome.tabs.create({ url: r.url, active: false })).id;
      }
    }
  } catch (e) {
    return terminar("error", `No se pudo abrir la página: ${e.message}`);
  }
  r.cargando = true;
  await guardarRonda(r);
  chrome.alarms.create("timeout", { delayInMinutes: TIMEOUT_CARGA_MIN });
}

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete") return;
  const r = await ronda();
  if (!r || tabId !== r.tabId || !r.cargando || !(tab.url || "").startsWith("https://www.zonaprop.com.ar/")) return;
  r.cargando = false;
  await guardarRonda(r);
  await chrome.alarms.clear("timeout");
  let html;
  try { html = await leer(tabId); }
  catch (e) { return cargaFallida(`no se pudo leer la página: ${e.message}`); }
  try { seguir(await api("/api/navegador/pagina", { ronda: r.id, url: r.url, html })); }
  catch (e) { terminar("error", e.message); }
});

// HTML de la pestaña. Si Cloudflare muestra su verificación, se espera hasta ~30 s a que se resuelva sola; si no,
// se manda igual y el servidor la registra como bloqueo (y espera el cooldown antes de volver a intentar).
async function leer(tabId) {
  await dormir(2500);                                           // deja terminar los scripts de la página
  for (let i = 0; ; i++) {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      // + los datos de los avisos que la página ya borró del DOM (ver captura.js)
      func: () => ({ titulo: document.title,
                     html: document.documentElement.outerHTML +
                           (window.__inmoDatos ? `\n<script id="preloadedData">${window.__inmoDatos}</script>` : "") }),
    });
    if (!DESAFIO.test(result.titulo) || i >= 6) return result.html;
    await dormir(5000);
  }
}

async function seguir(resp) {
  if (resp.fin) return terminar(resp.estado, resp.mensaje);
  const r = await ronda();
  if (!r) return;
  r.url = resp.siguiente;
  await guardarRonda(r);
  chrome.alarms.create("paso", { delayInMinutes: Math.max(resp.pausa || 0, MIN_PAUSA_S) / 60 });
}

async function cargaFallida(motivo) {
  const r = await ronda();
  if (!r) return;
  r.cargando = false;
  await guardarRonda(r);
  try { seguir(await api("/api/navegador/error", { ronda: r.id, url: r.url, motivo })); }
  catch (e) { terminar("error", e.message); }
}

async function cancelar() {
  const r = await ronda();
  if (!r) return;
  let resp = { mensaje: "Cancelada desde la extensión" };
  try { resp = await api("/api/navegador/cancelar", { ronda: r.id }); } catch { /* igual se corta acá */ }
  await terminar("cancelada", resp.mensaje);
}

async function terminar(estado, mensaje) {
  const r = await ronda();
  await chrome.alarms.clear("paso");
  await chrome.alarms.clear("timeout");
  await guardarRonda(null);
  if (r?.windowId != null) chrome.windows.remove(r.windowId).catch(() => {});
  else if (r?.tabId != null) chrome.tabs.remove(r.tabId).catch(() => {});
  await anotar(estado, mensaje);
}
