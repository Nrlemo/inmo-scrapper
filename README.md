# Inmo — monitor de propiedades

Dos piezas que comparten una SQLite:

- **`scrapper/`** — consulta portales (hoy Zonaprop) con cortesía (robots.txt, pausas aleatorias, User-Agent identificable, backoff),
  filtra según `scrapper/config/profiles.yaml`, y guarda publicaciones, historial de precios y duplicados entre portales.
- **`web/`** — FastAPI + Jinja2 + HTMX (sin build de JS), pensada para móvil: revisión rápida, lista con filtros, cambios de precio,
  comparador, mapa y pantalla *Estado* para lanzar el scrapper con selector de zonas y barra de progreso.

Todo corre en **una sola imagen Docker**; la web lanza el scrapper como subproceso.

## Inicio rápido (local, sin authentik)
    cp .env.example .env
    docker compose -f docker-compose.yml -f docker-compose.local.yml up --build     # http://127.0.0.1:8000

Con Podman: `podman-compose` o `podman build -t inmo-web .` y correrlo con `--userns=keep-id:uid=1000,gid=1000` y los volúmenes con `:Z`.
Los datos viven en `scrapper/data/` (SQLite + logs de corridas) y la configuración en `scrapper/config/profiles.yaml` (montada de sólo lectura).
El contenedor corre con UID 1000: si tu usuario es otro, `chown` de `scrapper/data`.

## Producción con Traefik + authentik
    cp .env.example .env    # completar INMO_DOMAIN, TRAEFIK_NETWORK, PROXY_SECRET (openssl rand -hex 32), ...
    docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d --build

La app **no tiene login propio**: confía en `X-authentik-username`, que sólo llega si el pedido pasó por Traefik + authentik.
Configuración paso a paso (Proxy Provider, outpost, nginx) en [`deploy/authentik.md`](deploy/authentik.md).
Reglas: no publicar el puerto de la app, definir `PROXY_SECRET`, y no definir `AUTH_DEV_USER` en producción.

## Variables de entorno
| Variable | Uso |
|---|---|
| `INMO_DB`, `INMO_CONFIG` | rutas de la SQLite y del YAML (en la imagen: `/data/inmo.sqlite`, `/config/profiles.yaml`) |
| `AUTH_USER_HEADER` / `AUTH_EMAIL_HEADER` | cabeceras de identidad (default `X-authentik-username` / `-email`) |
| `PROXY_SECRET` | si se define, exige `X-Proxy-Secret` igual en cada pedido |
| `RUN_ALLOWED_USERS` | usuarios que pueden lanzar/cancelar el scrapper (vacío = todos los autenticados) |
| `AUTH_DEV_USER` | **sólo desarrollo** |
| `PAGE_SIZE`, `TZ` | paginación; zona horaria (debe ser la misma para web y scrapper) |

## Scrapper
- Desde la web: **Estado → Ejecutar ahora**, con zonas a elegir. Muestra zona/página actual y % de avance, se puede cancelar.
  Las pausas entre páginas son deliberadas (20–150 s): una corrida completa puede tardar 30 min o más y los avisos se guardan al terminar.
- Un subconjunto de zonas no da de baja los avisos de las demás. «Ignorar intervalo mínimo» no salta el cooldown por bloqueo.
- Por CLI / cron: `docker compose run --rm web python -m inmo run --portal zonaprop` (con `PYTHONPATH=/srv/scrapper_src`).
- Agregar un portal: crear `scrapper/src/inmo/connectors/<portal>.py` (subclase de `Connector`, devuelve `SearchResult`) y registrarlo en `connectors/__init__.py`.

## Desarrollo
    cd scrapper && python -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
    cd web && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest && AUTH_DEV_USER=yo .venv/bin/uvicorn app.main:app --reload && .venv/bin/pytest

## Notas
- El mapa necesita `lat/lng`; se completan al volver a ver cada aviso en una corrida.
- El User-Agent del scrapper incluye un contacto (`scrapper/src/inmo/http.py`); cambialo si el repo es público.
- «Ver aviso embebido» usa un iframe a pedido: si el portal lo bloquea, usar «Abrir aviso original».
