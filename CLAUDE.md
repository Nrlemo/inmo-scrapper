Actuá como desarrollador full-stack senior. Esta es una aplicación web self-hosted que monitorea portales inmobiliarios, guarda los resultados en SQLite y me permite revisarlos y categorizarlos desde una interfaz web.

## Objetivo
1. Consultar periódicamente varios portales de compra/venta de inmuebles (y, desde la etapa 3, de alquiler).
2. Filtrar publicaciones según criterios predefinidos.
3. Guardar cada resultado en SQLite con fecha de consulta, detectando duplicados y cambios de precio.
4. Ofrecer una web donde yo pueda ver, filtrar y categorizar cada publicación.

## Cómo se traen los avisos: ronda por navegador
- **La única vía es la extensión del navegador** (Vivaldi/Chrome), en su propio repo: [Nrlemo/inmo-extension](https://github.com/Nrlemo/inmo-extension). Abre las búsquedas en el navegador real del usuario, página por página, y manda el HTML a la web (`/api/navegador/*`, token por usuario).
- **El servidor conduce la ronda** (`scrapper/src/inmo/navegador.py`): decide qué página sigue, cuánto esperar, guarda y da de baja. La extensión sólo abre, espera, lee y envía; los cambios de reglas no deberían requerir tocarla.
- **El scrapper HTTP (httpx/curl) está deprecado** (Cloudflare lo bloquea). No agregar código que pida páginas a los portales desde el servidor. Se quita en la etapa 1.
- **Conectores = parsers por página con interfaz común** (un módulo por portal en `connectors/`): URLs de búsqueda por zona, paginación, parseo de la página, detección de bloqueo. Agregar un portal no debería tocar ni la ronda ni la API de la extensión.
- **Scraping responsable, aunque sea desde el navegador:** respetar `robots.txt` (tope de páginas), pausas aleatorias entre páginas y zonas, cooldown tras un bloqueo, una ronda por día como máximo. Un portal que falla o bloquea no corta a los demás.

## Portales
- **Zonaprop:** funcionando por la extensión.
- **MercadoLibre Inmuebles:** la API oficial **no trae resultados (está bloqueada)**, ya lo investigamos. Va por la extensión, leyendo el listado web.
- **Argenprop:** corta enseguida las consultas agresivas. Va por la extensión con una cadencia bastante más prudente que Zonaprop (pausas largas, pocas páginas, cooldown largo).

## Criterios de búsqueda (configurables en un archivo YAML, no hardcodeados)
- Operación: [compra / alquiler]
- Tipo: [departamento / casa / PH]
- Zonas: [barrios o localidades]
- Precio: [mín – máx] en [USD / ARS]
- Ambientes: [mín – máx]
- Superficie cubierta: [mín m²]
- Extras: [cochera, balcón, apto crédito, antigüedad máx, etc.]
- Palabras clave a excluir: [ej. "a reciclar", "sin escritura"]
Debe poder existir más de un perfil de búsqueda a la vez.

## Base de datos (SQLite)
El esquema incluye al menos:
- publicaciones: id interno, portal, id_externo, url, título, dirección, barrio, precio, moneda, expensas, ambientes, m² cubiertos/totales, descripción, fotos (URLs), fecha_primera_vista, fecha_ultima_vista, activa (bool).
- consultas: id, perfil de búsqueda, fecha/hora, portal, cantidad de resultados, errores.
- historial_precios: publicación, precio, moneda, fecha.
- categorizacion: publicación, estado (nuevo / interesante / para visitar / descartado / contactado), puntaje 1–5, notas, etiquetas, fecha de modificación.
Reglas: clave única (portal, id_externo); si una publicación reaparece, actualizar fecha_ultima_vista; si cambia el precio, registrarlo en historial; si deja de aparecer en N consultas **completas** (no truncadas ni parciales), marcarla inactiva. Intentar detectar la misma propiedad publicada en distintos portales (dirección + m² + precio aproximado) y vincularlas.

**Desde la etapa 3:** compra y alquiler viven en **bases SQLite totalmente separadas**, con la autenticación (cuentas, sesiones, tokens de la extensión) en una base propia compartida.

## Backend
- Python 3.12, FastAPI, SQLAlchemy, selectolax para parsear. No hay Playwright ni cliente HTTP hacia los portales: el navegador del usuario es el que carga las páginas.
- Sin scheduler en el servidor: la extensión programa su ronda diaria. La web muestra el progreso y el historial de rondas (pantalla Estado).
- Logs claros y manejo de errores por conector: si un portal falla, los demás siguen.

## Frontend
- Listado de publicaciones con foto, precio, zona, m², portal y fecha.
- Filtros por estado, portal, rango de precio, zona, fecha, "nuevas desde la última visita" y "bajaron de precio".
- Categorización rápida (botones o atajos de teclado), puntaje, notas y etiquetas, guardado inmediato.
- Vista de detalle con historial de precios y link al aviso original.
- Diseño responsive (lo uso también desde el celular). Stack: HTMX + Jinja2, CSS propio.

## Despliegue
- Docker + docker-compose, con volumen persistente para la SQLite y el YAML de configuración.
- Variables de entorno para credenciales y parámetros.
- README con instalación, configuración y cómo agregar un conector nuevo.

## Hoja de ruta
Las fases originales (esquema + conector, scheduler, web, Docker y documentación) están cumplidas. Sigue esto, en orden; el detalle de cada punto está en los issues (label `etapa-N` en ambos repos).

**Etapa 1: sólo extensión.** Deprecar el scrapper HTTP y dejar la web funcionando únicamente con la ronda por navegador.
- inmo-scrapper: [#16](https://github.com/Nrlemo/inmo-scrapper/issues/16) deprecar el scrapper HTTP · [#17](https://github.com/Nrlemo/inmo-scrapper/issues/17) limpieza · [#18](https://github.com/Nrlemo/inmo-scrapper/issues/18) separar `main.py` en routers · [#19](https://github.com/Nrlemo/inmo-scrapper/issues/19) rendimiento
- inmo-extension: [#1](https://github.com/Nrlemo/inmo-extension/issues/1) la extensión como única vía (README, avisos de falla)

**Etapa 2: MercadoLibre y Argenprop en la extensión.**
- inmo-scrapper: [#20](https://github.com/Nrlemo/inmo-scrapper/issues/20) servidor multi-portal (interfaz de conector por páginas; va primero) · [#1](https://github.com/Nrlemo/inmo-scrapper/issues/1) conector MercadoLibre · [#2](https://github.com/Nrlemo/inmo-scrapper/issues/2) conector Argenprop · [#21](https://github.com/Nrlemo/inmo-scrapper/issues/21) duplicados entre portales
- inmo-extension: [#2](https://github.com/Nrlemo/inmo-extension/issues/2) multi-portal (permisos, captura y validación de pestaña por portal)

**Etapa 3: alquileres, con base de datos separada** y una capa de login que redirige a la base de compra o a la de alquiler.
- 3.1 Preparar la estructura del proyecto: [#22](https://github.com/Nrlemo/inmo-scrapper/issues/22) (bases separadas, auth compartida, engine por base, configuración y migración).
- 3.2 Programar el código: [#23](https://github.com/Nrlemo/inmo-scrapper/issues/23) (login con selector Compra/Alquiler, campos y filtros de alquiler) · inmo-extension [#3](https://github.com/Nrlemo/inmo-extension/issues/3) (rondas por base).

## Forma de trabajo
1. Antes de programar algo no trivial, proponé el enfoque (y los cambios de esquema, si hay) y esperá mi confirmación.
2. Trabajá por etapas y por issue, en commits chicos, con los tests verdes en cada paso (`scrapper/` y `web/`, cada uno con su `.venv`).
3. En cada entrega, código completo y ejecutable, sin placeholders.
4. Si un portal bloquea o su estructura es incierta, decímelo explícitamente en lugar de inventar selectores. Los parsers se prueban con HTML real guardado desde el navegador (fixtures).
5. Los cambios de la API `/api/navegador/*` tienen que ser compatibles con la versión de la extensión ya instalada (campos aditivos), o coordinarse con un issue en inmo-extension.
