# Instalación y configuración

Guía técnica de Inmo: cómo levantarlo, configurarlo y operarlo. La descripción de la funcionalidad está en el [README](../README.md).


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

Los datos viven en `scrapper/data/` (SQLite, que incluye usuarios y sesiones) y la configuración en `scrapper/config/profiles.yaml`.
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
Guía paso a paso (Proxy Provider, outpost, nginx) en [`deploy/authentik.md`](../deploy/authentik.md).
- ✅ No publiques el puerto de la app (la base no lo hace) y definí `PROXY_SECRET`: Traefik lo agrega a cada pedido y la app lo exige.

### Modo `none`
Sin usuarios ni contraseñas: la pantalla Estado muestra un aviso y el log lo advierte al arrancar. Si la URL es pública, cualquiera podrá ver y modificar todo.

## 🐳 Imagen de Docker

<table>
<tr><td><b>Imagen</b></td><td><a href="https://hub.docker.com/r/nrlemo/inmo-web"><code>nrlemo/inmo-web</code></a> en Docker Hub (pública)</td></tr>
<tr><td><b>Etiquetas</b></td><td><code>latest</code> (la última) y una por versión, con el hash corto del commit (por ejemplo <code>51fe5e2</code>). Para producción conviene fijar una versión.</td></tr>
<tr><td><b>Plataforma</b></td><td><code>linux/amd64</code> (no hay build para ARM todavía)</td></tr>
<tr><td><b>Tamaño</b></td><td>~68 MB comprimida al descargar (~290 MB en disco)</td></tr>
<tr><td><b>Base</b></td><td><code>python:3.12-slim</code>, corre como usuario sin privilegios (UID 1000)</td></tr>
<tr><td><b>Contenido</b></td><td>la web (FastAPI) y el paquete <code>inmo</code> (parsers y ronda por navegador). No incluye datos personales, configuración ni base de datos: todo eso se monta desde afuera.</td></tr>
<tr><td><b>Puerto</b></td><td><code>8000</code></td></tr>
<tr><td><b>Volúmenes</b></td><td><code>/data</code>: SQLite (avisos, usuarios, sesiones), escribible por UID 1000 · <code>/config</code>: <code>profiles.yaml</code> (se crea desde el ejemplo la primera vez; también escribible por UID 1000)</td></tr>
<tr><td><b>Healthcheck</b></td><td><code>GET /healthz</code> cada 30 s</td></tr>
</table>

### Con Docker Compose (recomendado)

Sin clonar el repo: alcanza con [`docker-compose.hub.yml`](../docker-compose.hub.yml) y, opcionalmente, un `.env` (ver [`.env.example`](../.env.example)). La primera vez la app crea `config/profiles.yaml` a partir del [ejemplo](../scrapper/config/profiles.example.yaml) incluido en la imagen.

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
      SESSION_REMEMBER_DAYS: ${SESSION_REMEMBER_DAYS:-90}
      TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}   # 0 = acceso directo; 1 detrás de Traefik/nginx
      COOKIE_SECURE: ${COOKIE_SECURE:-auto}
      PROXY_SECRET: ${PROXY_SECRET:-}
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

**Búsquedas** — `scrapper/config/profiles.yaml` (se crea sola a partir de `profiles.example.yaml` la primera vez; no se versiona porque contiene tu presupuesto y zonas): perfiles (operación, tipo, precio, ambientes, m², palabras a excluir), zonas por portal y parámetros de cortesía (pausas, páginas, intervalo entre rondas, cooldown).

**Variables de entorno**

| Variable | Uso |
|---|---|
| `INMO_DB`, `INMO_CONFIG` | Rutas de la SQLite y del YAML (en la imagen: `/data/inmo.sqlite`, `/config/profiles.yaml`) |
| `AUTH_MODE` | `basic` (default), `authentik` o `none` |
| `SETUP_TOKEN` | Modo `basic`: código de instalación (vacío = aleatorio, en el log) |
| `SESSION_IDLE_HOURS`, `SESSION_MAX_DAYS` | Modo `basic`: cierre por inactividad (8) y duración máxima de la sesión (7) |
| `SESSION_REMEMBER_DAYS` | Modo `basic`: duración de la sesión con «Mantener sesión iniciada» (90 días, sin cierre por inactividad) |
| `TRUSTED_PROXY_HOPS` | Proxies delante de la app (1 con Traefik; 0 si se accede directo). La IP del cliente se lee de `X-Forwarded-For` desde la derecha, así no se puede falsear para esquivar los límites de intentos |
| `COOKIE_SECURE` | `auto` (Secure si el pedido llega por https), `true` o `false` |
| `AUTH_DEFAULT_USER` | Modo `none`: nombre con el que actúan todos (`anonimo`) |
| `AUTH_USER_HEADER`, `AUTH_EMAIL_HEADER` | Modo `authentik`: cabeceras de identidad (por defecto `X-authentik-username` y `X-authentik-email`) |
| `PROXY_SECRET` | Modo `authentik`: exige `X-Proxy-Secret` igual en cada pedido |
| `PUID`, `PGID` | Usuario/grupo con el que corre el contenedor (default `1000:1000`); debe poder escribir la carpeta de datos |
| `PAGE_SIZE`, `TZ` | Paginación; zona horaria (las fechas se guardan locales, sin zona) |

## 📱 App Android

La web es una PWA (`manifest.json`, `sw.js`, íconos) y además hay una app nativa aparte, en el repo
[`inmo-android`](https://github.com/nrlemo/inmo-android): pantalla de login propia (llama a `POST /api/login`,
ver abajo) y un WebView a pantalla completa ya autenticado con esa sesión. Se instala como `.apk` sideloaded, sin
pasar por Play Store.

**`POST /api/login`** — sólo en `AUTH_MODE=basic`. Cuerpo `{"usuario": "...", "clave": "..."}`; devuelve 401 si
las credenciales no son válidas (mismo límite por IP y bloqueo progresivo que el login web) o, si son correctas,
la cookie de sesión (`cookie_name`/`cookie_value`/`max_age_seconds`/`secure`) para que la app la use directo en
su WebView — no hace falta abrir el formulario HTML desde la app.

## 🧭 Ronda por navegador (extensión)

Es la única forma de traer avisos: el servidor no pide páginas a los portales (el scrapper HTTP se quitó porque
Cloudflare lo bloqueaba). La extensión de [`inmo-extension`](https://github.com/Nrlemo/inmo-extension) (Vivaldi,
Chrome y otros basados en Chromium) abre las búsquedas en el navegador del usuario y manda cada página a
`/api/navegador/*`. El servidor conduce la ronda (`scrapper/src/inmo/navegador.py`): decide qué página sigue y cuánto
esperar, guarda los avisos y, al terminar una ronda completa, da de baja los que ya no aparecen.
- **Instalación:** ver el README de la extensión. **Token:** *Estado → Ronda por navegador → Generar token*. Hay uno
  por usuario y se guarda solo su hash.
- **Cuándo corre:** la extensión programa una ronda diaria (hora configurable en sus opciones, más una demora
  aleatoria). Para correr una en el momento: *Correr ronda ahora* en las opciones de la extensión.
- **Cortesía:** respeta `robots.txt` (tope de 5 páginas por búsqueda en Zonaprop), pausas aleatorias entre páginas y
  zonas (`page_delay` y `zone_delay` en el YAML, nunca menos de 30 s), `min_hours_between_runs` entre rondas y un
  cooldown de 24 h tras un bloqueo. No arranca si hay otra ronda en curso.
- **Progreso:** en *Estado* (zona, página, avisos hallados), con opción de cancelar. Lo recibido hasta ese momento
  queda guardado, pero una ronda cancelada o parcial no da de baja ningún aviso. Una ronda sin noticias de la
  extensión por 30 min queda «interrumpida».
- **Endpoints** (autenticados con `Authorization: Bearer <token>`, sin cookies ni CSRF):
  `GET /api/navegador/ping`, `POST /api/navegador/ronda`, `POST /api/navegador/pagina`,
  `POST /api/navegador/error` y `POST /api/navegador/cancelar`.
- Con authentik delante, `/api/navegador/` necesita la misma excepción que `/api/login`.

### Etiquetas automáticas
En `profiles.yaml`, la sección `auto_tags` asigna etiquetas según palabras clave del título o la descripción (ver `profiles.example.yaml`):
```yaml
auto_tags:
  patio: ["patio"]
  apto_credito: ["apto crédito", "apto credito", "apto banco"]
```
- No distingue mayúsculas ni tildes y busca por palabra completa. No cuentan las menciones negadas («sin cochera», «no tiene patio», «ni balcón»).
- Se guardan aparte de las etiquetas manuales: no las pisan. En la web se ven con borde punteado, y el listado tiene un filtro por etiqueta.
- Se recalculan sobre todo lo guardado al final de cada ronda, así que un cambio en las reglas se aplica solo. Para aplicarlo en el momento: `python -m inmo retag` (en Docker: `docker compose run --rm -e PYTHONPATH=/srv/scrapper_src web python -m inmo retag`).

### Si la ronda queda «bloqueada»
El portal mostró su verificación anti-bot (Cloudflare) y no se resolvió sola en ~30 s. La ronda se corta, queda
registrada como bloqueada y no se reintenta hasta que pase el cooldown (24 h). Si pasa seguido, entrá al portal a
mano desde ese mismo navegador y resolvé la verificación. No se implementa nada para esquivarla ni para rotar
identidades.

### Agregar un portal
Por ahora la ronda está atada a Zonaprop. La interfaz de conector por páginas, que permite sumar portales sin tocar
la ronda, está en [#20](https://github.com/Nrlemo/inmo-scrapper/issues/20); los conectores de MercadoLibre y Argenprop
en [#1](https://github.com/Nrlemo/inmo-scrapper/issues/1) y [#2](https://github.com/Nrlemo/inmo-scrapper/issues/2).

## 🛠️ Desarrollo

```bash
# scrapper
cd scrapper && python -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest

# web
cd web && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx   # httpx: TestClient
AUTH_MODE=none .venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest
```
