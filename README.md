<div align="center">

# 🏠 Inmo

**Monitor personal de propiedades en venta: junta las publicaciones, detecta cambios de precio y te ayuda a decidir.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3D72D7?logo=htmx&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![authentik](https://img.shields.io/badge/authentik-forward--auth-FD4B2D)

</div>

---

## ✨ Qué hace

Un **scrapper** cortés consulta los portales (hoy Zonaprop), filtra según tus criterios y guarda todo en SQLite.
Una **web liviana** pensada para el celular te deja revisar, marcar y comparar las publicaciones, entre varias personas.

| Pantalla | Para qué sirve |
|---|---|
| 📥 **Revisión** | Cola de publicaciones nuevas, una por vez. Favorita, descartada o contactada con un toque o con las teclas `F` `D` `C` `→`. |
| 📋 **Todas** | Búsqueda de texto y filtros: barrio, precio, m², ambientes, cochera, estado, «bajó de precio», «nuevas desde mi última visita». Búsquedas guardadas y export a CSV. |
| 📉 **Precios** | Feed de subas y bajas con el porcentaje, y gráfico del historial en cada aviso. |
| ⚖️ **Comparar** | Tus favoritas lado a lado, con USD/m² y diferencia contra la mediana del barrio. |
| 🗺️ **Mapa** | Todos los avisos con filtros y colores por estado (favorita, contactada, descartada, bajó de precio). |
| 🔎 **Detalle** | Notas, puntaje 1–5, etiquetas, marca de contactada, avisos repetidos en otros portales y link al original. |
| ⚙️ **Estado** | Lanzá el scrapper eligiendo zonas, con barra de progreso, cancelación e historial de corridas. Activá una **ejecución automática diaria** de madrugada. |

También: **multiusuario** (el estado es compartido y cada cambio registra quién lo hizo), modo oscuro y navegación inferior en móvil.

## 📸 Capturas

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/lista.png" alt="Lista de publicaciones con filtros"><br><sub><b>Todas</b> · búsqueda y filtros</sub></td>
    <td width="50%"><img src="docs/screenshots/mapa.png" alt="Mapa con avisos agrupados"><br><sub><b>Mapa</b> · avisos agrupados y filtros</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/comparar.png" alt="Comparador de favoritas"><br><sub><b>Comparar</b> · USD/m² y diferencia con la mediana del barrio</sub></td>
    <td width="50%"><img src="docs/screenshots/estado.png" alt="Pantalla de estado con selector de zonas"><br><sub><b>Estado</b> · ejecutar el scrapper eligiendo zonas</sub></td>
  </tr>
</table>

## 🧩 Arquitectura

```
                    ┌──────────────┐   forward auth    ┌────────────────┐
   navegador ─────▶ │   Traefik    │ ◀───────────────▶ │   authentik    │
                    └──────┬───────┘                   └────────────────┘
                           │  X-authentik-username
                           ▼
              ┌─────────────────────────────┐
              │  contenedor  inmo-web       │
              │  ┌───────────┐  subproceso  ┌───────────┐
              │  │ web       │ ───────────▶ │ scrapper  │──▶ Zonaprop
              │  │ FastAPI   │              │ (CLI)     │
              │  └─────┬─────┘              └─────┬─────┘
              └────────┼──────────────────────────┼─────┘
                       └──────────┐   ┌───────────┘
                              ┌───▼───▼───┐
                              │  SQLite   │  volumen persistente
                              └───────────┘
```

- **`scrapper/`** — conectores por portal (interfaz común), historial de precios, detección de duplicados entre portales y baja de avisos que dejan de aparecer.
- **`web/`** — FastAPI + Jinja2 + HTMX, sin build de JS. HTMX y Leaflet se sirven localmente.
- **Una sola imagen Docker**: la web lanza el scrapper como subproceso y le sigue el progreso por la base.

## 🚀 Inicio rápido

Probar en tu máquina, sin authentik:

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
# → http://127.0.0.1:8000
```

> **Podman:** `podman build -t inmo-web .` y correrlo con `--userns=keep-id:uid=1000,gid=1000` y los volúmenes con `:Z`.

Los datos viven en `scrapper/data/` (SQLite y logs de corridas) y la configuración en `scrapper/config/profiles.yaml`.
El contenedor corre con UID 1000: si tu usuario es otro, hacé `chown` de `scrapper/data`.

## 🔐 Producción con Traefik + authentik

```bash
cp .env.example .env    # INMO_DOMAIN, TRAEFIK_NETWORK, PROXY_SECRET (openssl rand -hex 32), ...
docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d --build
```

La app **no tiene login propio**: confía en la cabecera `X-authentik-username`, que solo llega si el pedido pasó por el proxy.

- ✅ No publiques el puerto de la app (la base no lo hace).
- ✅ Definí `PROXY_SECRET`: Traefik lo agrega a cada pedido y la app lo exige.
- ⛔ No definas `AUTH_DISABLED` en producción expuesta a internet.

**¿Sin autenticación?** Para una red privada o si ya protegés el acceso por otro lado: `AUTH_DISABLED=true`. Nadie inicia sesión y todos
actúan como `AUTH_DEFAULT_USER` (por defecto `anonimo`), salvo que el proxy envíe `X-authentik-username`. En ese modo no se exige
`PROXY_SECRET`, y la pantalla Estado muestra un aviso. Con el override de Traefik agregá `INMO_MIDDLEWARES=` (vacío) para quitar authentik.
Como todos son el mismo usuario, `RUN_ALLOWED_USERS` no aplica.

Guía completa (Proxy Provider, outpost, nginx): [`deploy/authentik.md`](deploy/authentik.md).

## ⚙️ Configuración

**Búsquedas** — `scrapper/config/profiles.yaml`: perfiles (operación, tipo, precio, ambientes, m², palabras a excluir), zonas por portal y parámetros de cortesía (pausas, páginas, horarios).

**Variables de entorno**

| Variable | Uso |
|---|---|
| `INMO_DB`, `INMO_CONFIG` | Rutas de la SQLite y del YAML (en la imagen: `/data/inmo.sqlite`, `/config/profiles.yaml`) |
| `AUTH_USER_HEADER`, `AUTH_EMAIL_HEADER` | Cabeceras de identidad (por defecto `X-authentik-username` y `X-authentik-email`) |
| `PROXY_SECRET` | Si se define, exige `X-Proxy-Secret` igual en cada pedido |
| `RUN_ALLOWED_USERS` | Usuarios que pueden lanzar o cancelar el scrapper (vacío = todos los autenticados) |
| `AUTH_DISABLED`, `AUTH_DEFAULT_USER` | Modo **sin autenticación** (default `false`) y nombre con el que actúan todos (`anonimo`) |
| `SCHEDULER_ENABLED` | Habilita el programador diario (default `true`); se prende/apaga desde la pantalla Estado |
| `PAGE_SIZE`, `TZ` | Paginación; zona horaria (la misma para web y scrapper, las fechas son locales) |

## 🕷️ El scrapper

- **Desde la web:** *Estado → Ejecutar ahora*. Con un subconjunto de zonas no se dan de baja los avisos de las demás.
- **Automático:** en *Estado → Ejecución automática diaria* elegís la hora (por defecto 03:00) y lo activás o desactivás. Se le suma una demora aleatoria de hasta 30 min, distinta cada día. Corre todas las zonas de todos los portales. Si la web estuvo apagada y se pasó la hora por más de 3 h, salta al día siguiente; y si hubo una corrida en las últimas 20 h, la automática se omite (cortesía con el portal). Queda guardado en la base, así que sobrevive a reinicios.
- **Por CLI o cron externo:** `docker compose run --rm -e PYTHONPATH=/srv/scrapper_src web python -m inmo run --portal zonaprop`
- **Cortesía:** respeta `robots.txt`, usa un User-Agent identificable, pausas aleatorias de 20 a 150 s y reintentos con backoff. Si el portal bloquea, se frena y entra en cooldown.
- **Una corrida completa puede tardar 30 minutos o más**; los avisos se guardan al terminar.

### Agregar un portal

1. Crear `scrapper/src/inmo/connectors/<portal>.py` con una subclase de `Connector` cuyo `search(profile)` devuelva un `SearchResult` (sin lanzar excepciones: los errores van en el resultado).
2. Registrarlo en `scrapper/src/inmo/connectors/__init__.py`.
3. Agregar el portal y sus zonas al perfil en `profiles.yaml`. Aparece solo en el selector de la pantalla Estado.

## 🛠️ Desarrollo

```bash
# scrapper
cd scrapper && python -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest

# web
cd web && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
AUTH_DISABLED=true .venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest
```

## 📝 Notas

- El **mapa** necesita coordenadas: se completan cuando cada aviso se vuelve a ver en una corrida.
- «Ver aviso embebido» usa un iframe a pedido; si el portal lo bloquea, usá «Abrir aviso original».
- El User-Agent del scrapper incluye un contacto (`scrapper/src/inmo/http.py`); cambialo si el repo se vuelve público.
- Uso personal y de bajo volumen: respetá los términos de cada portal.
