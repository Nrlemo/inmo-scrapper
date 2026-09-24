# Inmo · ronda por navegador

Extensión para Vivaldi, Chrome, Edge o cualquier navegador basado en Chromium. Una vez por día abre tus búsquedas
de Zonaprop en una ventana minimizada de **tu propio navegador**, página por página, y las carga en tu servidor de
Inmo. Reemplaza al scrapper HTTP, que Cloudflare bloquea seguido.

- **Mismas reglas que el scrapper:** usa las zonas de `profiles.yaml`, el tope de 5 páginas por búsqueda
  (`robots.txt`), los filtros del perfil y las pausas de cortesía (30 s a 2,5 min). Solo da de baja un aviso después
  de una ronda completa.
- **El servidor conduce la ronda:** la extensión solo abre, espera, lee y envía. Qué página sigue y cuánto esperar lo
  decide Inmo, así que los cambios de configuración no requieren tocar la extensión.
- **Una ronda por día como máximo:** respeta el mismo intervalo mínimo (20 h) y el cooldown tras un bloqueo que el
  scrapper.

## Instalación

1. En Inmo: **Estado → Ronda por navegador → Generar token**. Copialo: se muestra una sola vez.
2. En Vivaldi, abrí `vivaldi://extensions` (en Chrome, `chrome://extensions`) y activá **Modo de desarrollador**.
3. **Cargar extensión descomprimida** y elegí esta carpeta (`extension/`).
4. Se abre la pantalla de opciones. Si no, tocá el ícono de Inmo en la barra. Completá:
   - **Servidor:** la dirección con la que abrís Inmo, por ejemplo `https://inmo.midominio.com`. Al guardar, el
     navegador pide permiso para conectarse a ese servidor.
   - **Token:** el del paso 1.
   - **Hora de la ronda diaria:** por defecto 03:30, más una demora aleatoria de hasta 20 min.
5. **Probar conexión** tiene que responder «Conectado como …».
6. Opcional: **Correr ronda ahora** para ver la primera ronda. El avance aparece también en Inmo → Estado.

## Cómo se comporta

- Necesita el **navegador abierto**. Si a la hora programada estaba cerrado, la ronda corre unos minutos después de
  abrirlo. Si se cerró a mitad de una ronda, la retoma donde estaba. Si pasan más de 30 min sin noticias, el servidor
  la da por interrumpida.
- Abre las páginas en una **ventana minimizada**, que se cierra sola al terminar. Podés seguir usando el navegador,
  pero no cierres esa ventana mientras corre.
- Si Zonaprop muestra la verificación de Cloudflare, espera hasta ~30 s a que se resuelva sola. Si no se resuelve, la
  ronda se registra como **bloqueada** y no se reintenta hasta que pase el cooldown (24 h). Si esto pasa seguido,
  entrá a Zonaprop a mano desde este navegador y resolvé la verificación.
- Una ronda completa tarda lo mismo que el scrapper: entre 30 y 60 min según la cantidad de zonas.

## Actualizar

Después de actualizar el repo, en `vivaldi://extensions` tocá **Recargar** (↻) en la tarjeta de Inmo. La
configuración se conserva.

## Detrás de authentik

Si Inmo está protegido con authentik (forward auth), agregá una excepción para `/api/navegador/`, igual que para
`/api/login`. La extensión se autentica con su token.
