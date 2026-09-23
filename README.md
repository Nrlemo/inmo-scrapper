<div align="center">

# 🏠 Inmo

**Monitor personal de propiedades en venta: junta las publicaciones, detecta cambios de precio y te ayuda a decidir.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-3D72D7?logo=htmx&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![authentik](https://img.shields.io/badge/authentik-forward--auth-FD4B2D)
[![Docker Hub](https://img.shields.io/badge/Docker%20Hub-nrlemo%2Finmo--web-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/nrlemo/inmo-web)

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

También: **multiusuario** (el estado es compartido y cada cambio registra quién lo hizo; login propio, authentik o sin autenticación), modo oscuro y navegación inferior en móvil.

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
- **Una sola imagen Docker** ([`nrlemo/inmo-web`](https://hub.docker.com/r/nrlemo/inmo-web) en Docker Hub): la web lanza el scrapper como subproceso y le sigue el progreso por la base.

## 🔐 Autenticación: tres modos

Se elige con `AUTH_MODE` en `.env`:

| Modo | Para qué | Cómo se levanta |
|---|---|---|
| **`basic`** (por defecto) | Login propio con usuario y contraseña. Asistente de instalación y administración de usuarios. | `docker compose -f docker-compose.yml -f docker-compose.local.yml up --build` (o con `docker-compose.traefik.yml` detrás de TLS) |
| **`authentik`** | Delegar el login en authentik (SSO, MFA, grupos) por forward auth. | `docker compose -f docker-compose.yml -f docker-compose.traefik.yml -f docker-compose.authentik.yml up -d --build` |
| **`none`** | Sin autenticación: red privada o protección externa. Todos actúan como `AUTH_DEFAULT_USER`. | `AUTH_MODE=none` + `docker-compose.local.yml` o `docker-compose.traefik.yml` |

```bash
cp .env.example .env          # elegí AUTH_MODE y completá lo que corresponda
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build    # → http://127.0.0.1:8000
```

La primera vez se crea `scrapper/config/profiles.yaml` a partir de `profiles.example.yaml`: editalo con tus zonas y presupuesto (no hace falta reiniciar; no se versiona).

> **Podman:** `podman build -t inmo-web .` y correrlo con `--userns=keep-id:uid=1000,gid=1000` y los volúmenes con `:Z`.

Los datos viven en `scrapper/data/` (SQLite, que incluye usuarios y sesiones, y logs de corridas) y la configuración en `scrapper/config/profiles.yaml`.
El contenedor corre con UID 1000: si tu usuario es otro, hacé `chown` de `scrapper/data`.

### Modo `basic`: primer inicio
1. Al levantar por primera vez, cualquier página redirige a **`/setup`**, una ventana simple para crear el **administrador**.
2. Pide un **código de instalación** para que nadie más pueda reclamar la cuenta si la URL ya está expuesta: lo imprime el servidor en el log
   (`docker compose logs web | grep INSTALACI`) o es el valor de `SETUP_TOKEN` si lo definiste.
3. Desde **Cuenta → Administrar usuarios** (solo admin) se crean, desactivan y restablecen usuarios, y se les da rol de administrador.
   Las cuentas nuevas reciben una contraseña temporal (generada, se muestra una sola vez) y deben cambiarla al primer ingreso.

**Protecciones del login por formulario**
- Contraseñas con **argon2id**; mínimo 12 caracteres, sin claves triviales ni que contengan el usuario.
- Sesiones **del lado del servidor** (solo se guarda el hash del token), cookie `HttpOnly`, `SameSite=Lax` y `Secure` con prefijo `__Host-` bajo HTTPS; sesión nueva en cada login; cierre por inactividad (8 h) y duración máxima (7 días).
- **CSRF** con token por sesión en todos los pedidos que modifican datos, y doble envío firmado en el login.
- **Fuerza bruta:** límite de intentos por IP y bloqueo creciente de la cuenta (15 min, 30, 60… hasta 24 h) tras 5 fallos seguidos; mensaje de error genérico y tiempo de respuesta igualado para no revelar qué usuarios existen.
- Cambiar la contraseña o desactivar a un usuario cierra sus otras sesiones; siempre queda al menos un administrador activo.
- Redirección post-login solo a rutas propias (sin *open redirect*), `Cache-Control: no-store`, HSTS y CSP estricta.
- Recomendado: publicarlo detrás de un proxy con **HTTPS** (Traefik). Sin HTTPS las cookies no pueden llevar `Secure`.

### Modo `authentik`
La app **no tiene login propio** en este modo: confía en `X-authentik-username`, que solo llega si el pedido pasó por Traefik + authentik.
Guía paso a paso (Proxy Provider, outpost, nginx) en [`deploy/authentik.md`](deploy/authentik.md).
- ✅ No publiques el puerto de la app (la base no lo hace) y definí `PROXY_SECRET`: Traefik lo agrega a cada pedido y la app lo exige.

### Modo `none`
Sin usuarios ni contraseñas: la pantalla Estado muestra un aviso y el log lo advierte al arrancar. Si la URL es pública, cualquiera podrá ver, modificar y lanzar el scrapper.

## 🐳 Imagen de Docker

<table>
<tr><td><b>Imagen</b></td><td><a href="https://hub.docker.com/r/nrlemo/inmo-web"><code>nrlemo/inmo-web</code></a> en Docker Hub (pública)</td></tr>
<tr><td><b>Etiquetas</b></td><td><code>latest</code> (la última) y una por versión, con el hash corto del commit (por ejemplo <code>51fe5e2</code>). Para producción conviene fijar una versión.</td></tr>
<tr><td><b>Plataforma</b></td><td><code>linux/amd64</code> (no hay build para ARM todavía)</td></tr>
<tr><td><b>Tamaño</b></td><td>~68 MB comprimida al descargar (~290 MB en disco)</td></tr>
<tr><td><b>Base</b></td><td><code>python:3.12-slim</code>, corre como usuario sin privilegios (UID 1000)</td></tr>
<tr><td><b>Contenido</b></td><td>la web (FastAPI) y el scrapper. No incluye datos personales, configuración ni base de datos: todo eso se monta desde afuera.</td></tr>
<tr><td><b>Puerto</b></td><td><code>8000</code></td></tr>
<tr><td><b>Volúmenes</b></td><td><code>/data</code>: SQLite (avisos, usuarios, sesiones) y logs de corridas (escribible por UID 1000) · <code>/config</code>: <code>profiles.yaml</code> (se crea desde el ejemplo la primera vez; también escribible por UID 1000)</td></tr>
<tr><td><b>Healthcheck</b></td><td><code>GET /healthz</code> cada 30 s</td></tr>
</table>

### Con Docker Compose (recomendado)

Sin clonar el repo: alcanza con [`docker-compose.hub.yml`](docker-compose.hub.yml) y, opcionalmente, un `.env` (ver [`.env.example`](.env.example)). La primera vez la app crea `config/profiles.yaml` a partir del [ejemplo](scrapper/config/profiles.example.yaml) incluido en la imagen.

```bash
mkdir -p data config                                   # en una carpeta de trabajo
sudo chown 1000:1000 data config                       # el contenedor corre con UID 1000 (o usá PUID/PGID, ver abajo)
docker compose -f docker-compose.hub.yml up -d         # → http://127.0.0.1:8000
nano config/profiles.yaml                              # se creó solo: ajustá zonas y presupuesto (sin reiniciar)
docker compose -f docker-compose.hub.yml logs web | grep INSTALACI    # código para crear el administrador
```

<details><summary><code>docker-compose.hub.yml</code></summary>

```yaml
services:
  web:
    image: nrlemo/inmo-web:${INMO_TAG:-latest}
    restart: unless-stopped
    ports: ["127.0.0.1:8000:8000"]     # detrás de un proxy inverso, quitá esta línea y usá `expose`/labels
    environment:
      TZ: ${TZ:-America/Argentina/Buenos_Aires}
      AUTH_MODE: ${AUTH_MODE:-basic}                 # basic | none | authentik
      AUTH_DEFAULT_USER: ${AUTH_DEFAULT_USER:-anonimo}
      SETUP_TOKEN: ${SETUP_TOKEN:-}                  # modo basic: código de instalación (vacío = aleatorio, en el log)
      SESSION_IDLE_HOURS: ${SESSION_IDLE_HOURS:-8}
      SESSION_MAX_DAYS: ${SESSION_MAX_DAYS:-7}
      TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}   # 0 = acceso directo; 1 detrás de Traefik/nginx
      COOKIE_SECURE: ${COOKIE_SECURE:-auto}
      PROXY_SECRET: ${PROXY_SECRET:-}
      INMO_CONTACT: ${INMO_CONTACT:-}                # contacto para el User-Agent del scrapper
      RUN_ALLOWED_USERS: ${RUN_ALLOWED_USERS:-}
      SCHEDULER_ENABLED: ${SCHEDULER_ENABLED:-true}
    volumes:
      - ./data:/data
      - ./config:/config                             # profiles.yaml: se crea solo desde el ejemplo
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
```
</details>

### Con `docker run`

```bash
docker run -d --name inmo --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -e TZ=America/Argentina/Buenos_Aires \
  -e TRUSTED_PROXY_HOPS=0 \
  -e INMO_CONTACT=tu@email.com \
  -v "$PWD/data:/data" -v "$PWD/config:/config:ro" \
  nrlemo/inmo-web:latest
```

Las variables de entorno están descritas en la sección Configuración, más abajo. Para otro modo de autenticación agregá `-e AUTH_MODE=none` o `authentik`.

### Si algo falla
| Síntoma | Causa y solución |
|---|---|
| `sqlite3.OperationalError: unable to open database file` al arrancar | El usuario del contenedor no puede escribir `./data`. Suele pasar cuando Docker crea `./data` solo (queda como `root`) o cuando tu usuario no es el UID 1000. Solución: `sudo chown -R 1000:1000 data`, **o** correr con tu usuario poniendo `PUID=$(id -u)` y `PGID=$(id -g)` en el `.env`. Con SELinux, agregá `:Z` al volumen. |
| `/estado` dice que falta `profiles.yaml` | Normalmente se crea solo al arrancar. Si `/config` está montado como solo lectura o sin permisos, el log lo avisa: dale permiso de escritura o copiá `profiles.example.yaml` a `config/profiles.yaml`. |
| Las cookies de sesión no se guardan / el login vuelve a pedirse | Accediendo por `http://` desde otra máquina las cookies `Secure` no se aceptan: usá HTTPS (proxy inverso) o `COOKIE_SECURE=false` solo en una red de confianza. |

### Actualizar
```bash
docker compose -f docker-compose.hub.yml pull && docker compose -f docker-compose.hub.yml up -d
```
Los datos viven en `./data`, así que sobreviven a la actualización. Para volver a una versión anterior: `INMO_TAG=51fe5e2 docker compose -f docker-compose.hub.yml up -d`.

### Construir tu propia imagen
```bash
docker build -t inmo-web .        # desde la raíz del repo
```
Para publicarla: `docker tag inmo-web TU_USUARIO/inmo-web:latest && docker push TU_USUARIO/inmo-web:latest`. Los compose del repo (`docker-compose.yml` y sus complementos `local`, `traefik` y `authentik`) construyen la imagen localmente en lugar de bajarla.

## ⚙️ Configuración

**Búsquedas** — `scrapper/config/profiles.yaml` (se crea sola a partir de `profiles.example.yaml` la primera vez; no se versiona porque contiene tu presupuesto y zonas): perfiles (operación, tipo, precio, ambientes, m², palabras a excluir), zonas por portal y parámetros de cortesía (pausas, páginas, horarios).

**Variables de entorno**

| Variable | Uso |
|---|---|
| `INMO_DB`, `INMO_CONFIG` | Rutas de la SQLite y del YAML (en la imagen: `/data/inmo.sqlite`, `/config/profiles.yaml`) |
| `AUTH_MODE` | `basic` (default), `authentik` o `none` |
| `SETUP_TOKEN` | Modo `basic`: código de instalación (vacío = aleatorio, en el log) |
| `SESSION_IDLE_HOURS`, `SESSION_MAX_DAYS` | Modo `basic`: cierre por inactividad (8) y duración máxima de la sesión (7) |
| `TRUSTED_PROXY_HOPS` | Proxies delante de la app (1 con Traefik; 0 si se accede directo). La IP del cliente se lee de `X-Forwarded-For` desde la derecha, así no se puede falsear para esquivar los límites de intentos |
| `COOKIE_SECURE` | `auto` (Secure si el pedido llega por https), `true` o `false` |
| `AUTH_DEFAULT_USER` | Modo `none`: nombre con el que actúan todos (`anonimo`) |
| `AUTH_USER_HEADER`, `AUTH_EMAIL_HEADER` | Modo `authentik`: cabeceras de identidad (por defecto `X-authentik-username` y `X-authentik-email`) |
| `PROXY_SECRET` | Modo `authentik`: exige `X-Proxy-Secret` igual en cada pedido |
| `RUN_ALLOWED_USERS` | Usuarios que pueden lanzar el scrapper y cambiar la programación (vacío = todos los autenticados) |
| `PUID`, `PGID` | Usuario/grupo con el que corre el contenedor (default `1000:1000`); debe poder escribir la carpeta de datos |
| `INMO_HTTP_CLIENT` | Cliente HTTP del scrapper: `httpx` (default) o `curl` (ver «Si Zonaprop responde 403») |
| `INMO_CONTACT` | Email o URL que va en el User-Agent del scrapper (dato personal; vacío = sin contacto) |
| `SCHEDULER_ENABLED` | Habilita el programador diario (default `true`); se prende/apaga desde la pantalla Estado |
| `PAGE_SIZE`, `TZ` | Paginación; zona horaria (la misma para web y scrapper, las fechas son locales) |

## 🕷️ El scrapper

- **Desde la web:** *Estado → Ejecutar ahora*. Con un subconjunto de zonas no se dan de baja los avisos de las demás.
- **Automático:** en *Estado → Ejecución automática diaria* elegís la hora (por defecto 03:00) y lo activás o desactivás. Se le suma una demora aleatoria de hasta 30 min, distinta cada día. Corre todas las zonas de todos los portales. Si la web estuvo apagada y se pasó la hora por más de 3 h, salta al día siguiente; y si hubo una corrida en las últimas 20 h, la automática se omite (cortesía con el portal). Queda guardado en la base, así que sobrevive a reinicios.
- **Por CLI o cron externo:** `docker compose run --rm -e PYTHONPATH=/srv/scrapper_src web python -m inmo run --portal zonaprop`
- **Cortesía:** respeta `robots.txt`, usa un User-Agent identificable, pausas aleatorias de 20 a 150 s y reintentos con backoff. Si el portal bloquea, se frena y entra en cooldown.
- **Una corrida completa puede tardar 30 minutos o más**; los avisos se guardan al terminar.

### Si Zonaprop responde 403
Zonaprop usa Cloudflare, que además de la IP mira **cómo se presenta el cliente**. Puede pasar que acepte a un cliente y rechace a otro desde la misma red (en las pruebas: `httpx` HTTP/2 recibía un desafío; `httpx` HTTP/1.1 pasaba en un lado y no en otro). Qué hacer:
1. **Esperar.** Los bloqueos son temporales y cada intento nuevo los empeora: tras un `403` el scrapper entra en cooldown de 24 h. No lances varias corridas seguidas.
2. **Probar con una sola zona** antes de lanzar todas.
3. Si un solo pedido con `curl` pasa pero el scrapper no, activá `INMO_HTTP_CLIENT=curl` (en `.env`): el scrapper pide las páginas con el binario `curl` incluido en la imagen, con **el mismo User-Agent honesto, las mismas pausas y la misma detección de bloqueos**; solo cambia el cliente de red. Es más frágil que `httpx`: depende de que el sitio siga aceptándolo.
4. También podés correr el scrapper desde otra conexión (por ejemplo tu PC de casa).

No se implementa nada para imitar a un navegador ni para rotar identidades: no es el uso responsable para el que se diseñó esto.

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
AUTH_MODE=none .venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest
```

## 📝 Notas

- El **mapa** necesita coordenadas: se completan cuando cada aviso se vuelve a ver en una corrida.
- «Ver aviso embebido» usa un iframe a pedido; si el portal lo bloquea, usá «Abrir aviso original».
- Uso personal y de bajo volumen: respetá los términos de cada portal.
