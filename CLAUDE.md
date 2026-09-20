Actuá como desarrollador full-stack senior. Vas a construir una aplicación web self-hosted que monitorea portales inmobiliarios, guarda los resultados en SQLite y me permite revisarlos y categorizarlos desde una interfaz web.

## Objetivo
1. Consultar periódicamente varios portales de compra/venta de inmuebles.
2. Filtrar publicaciones según criterios predefinidos.
3. Guardar cada resultado en SQLite con fecha de consulta, detectando duplicados y cambios de precio.
4. Ofrecer una web donde yo pueda ver, filtrar y categorizar cada publicación.

## Portales (orden de prioridad)
- MercadoLibre Inmuebles: usar la API oficial si está disponible (verificar requisitos de autenticación actuales).
- Zonaprop y Argenprop: si no hay API pública, usar scraping responsable (respetar robots.txt, rate limiting con delays aleatorios, User-Agent identificable, reintentos con backoff).
- Arquitectura de "conectores" (un módulo por portal con una interfaz común) para poder agregar portales nuevos sin tocar el resto.

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
Diseñá el esquema con al menos:
- publicaciones: id interno, portal, id_externo, url, título, dirección, barrio, precio, moneda, expensas, ambientes, m² cubiertos/totales, descripción, fotos (URLs), fecha_primera_vista, fecha_ultima_vista, activa (bool).
- consultas: id, perfil de búsqueda, fecha/hora, portal, cantidad de resultados, errores.
- historial_precios: publicación, precio, moneda, fecha.
- categorizacion: publicación, estado (nuevo / interesante / para visitar / descartado / contactado), puntaje 1–5, notas, etiquetas, fecha de modificación.
Reglas: clave única (portal, id_externo); si una publicación reaparece, actualizar fecha_ultima_vista; si cambia el precio, registrarlo en historial; si deja de aparecer en N consultas, marcarla inactiva. Intentar detectar la misma propiedad publicada en distintos portales (dirección + m² + precio aproximado) y vincularlas.

## Backend
- Python 3.12, FastAPI, SQLAlchemy (o sqlite3 directo si es más simple), httpx, BeautifulSoup/selectolax; Playwright solo si un portal lo exige.
- Scheduler (APScheduler o cron) con frecuencia configurable.
- Endpoint para disparar una consulta manual.
- Logs claros y manejo de errores por conector: si un portal falla, los demás siguen.

## Frontend
- Listado de publicaciones con foto, precio, zona, m², portal y fecha.
- Filtros por estado, portal, rango de precio, zona, fecha, "nuevas desde la última visita" y "bajaron de precio".
- Categorización rápida (botones o atajos de teclado), puntaje, notas y etiquetas, guardado inmediato.
- Vista de detalle con historial de precios y link al aviso original.
- Diseño responsive (lo voy a usar también desde el celular). Stack simple: HTMX + Tailwind, o Vue si lo justificás.

## Despliegue
- Docker + docker-compose, con volumen persistente para la SQLite y el YAML de configuración.
- Variables de entorno para credenciales y parámetros.
- README con instalación, configuración y cómo agregar un conector nuevo.

## Forma de trabajo
1. Antes de programar, proponé la arquitectura y el esquema de la base, y esperá mi confirmación.
2. Implementá por fases: (a) esquema + un conector funcionando, (b) resto de conectores + scheduler, (c) interfaz web, (d) Docker y documentación.
3. En cada fase entregá código completo y ejecutable, sin placeholders.
4. Si un portal bloquea el scraping o su estructura es incierta, decímelo explícitamente en lugar de inventar selectores.