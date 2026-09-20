"""Configuración por variables de entorno."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# El paquete `inmo` (scrapper) aporta los modelos de la SQLite compartida.
_src = os.environ.get("INMO_SRC") or str(ROOT.parent / "scrapper" / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

DB_PATH = os.environ.get("INMO_DB", str(ROOT.parent / "scrapper" / "data" / "inmo.sqlite"))
# Cabeceras que inyecta authentik (forward-auth). El proxy debe ser el único camino a la app.
USER_HEADER = os.environ.get("AUTH_USER_HEADER", "X-authentik-username")
EMAIL_HEADER = os.environ.get("AUTH_EMAIL_HEADER", "X-authentik-email")
# Modo de autenticación:
#   basic     (default) formulario de usuario/contraseña propio, con instalación inicial y administración de usuarios
#   authentik el proxy autentica (forward-auth) y envía la identidad en cabeceras
#   none      sin autenticación: todos actúan como AUTH_DEFAULT_USER (sólo red privada / otra protección delante)
AUTH_MODE = os.environ.get("AUTH_MODE", "basic").strip().lower()
if AUTH_MODE not in ("basic", "authentik", "none"):
    raise RuntimeError(f"AUTH_MODE inválido: {AUTH_MODE!r} (usar basic, authentik o none)")
DEFAULT_USER = os.environ.get("AUTH_DEFAULT_USER", "anonimo")
# Código exigido en la instalación inicial (modo basic). Si no se define, se genera y se imprime en el log.
SETUP_TOKEN = os.environ.get("SETUP_TOKEN") or None
# Cantidad de proxies inversos delante de la app (Traefik = 1; acceso directo = 0). La IP del cliente se toma
# de X-Forwarded-For contando desde la derecha (lo que agregan nuestros proxies), nunca de lo que envía el cliente.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
SESSION_IDLE_HOURS = float(os.environ.get("SESSION_IDLE_HOURS", "8"))     # cierre por inactividad
SESSION_MAX_DAYS = float(os.environ.get("SESSION_MAX_DAYS", "7"))         # duración máxima absoluta
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").strip().lower()   # auto (según https) | true | false
# Secreto opcional compartido con el proxy (cabecera X-Proxy-Secret) como defensa extra.
PROXY_SECRET = os.environ.get("PROXY_SECRET")
# Configuración de perfiles/zonas del scrapper (la misma que usa el CLI).
CONFIG_PATH = os.environ.get("INMO_CONFIG", str(ROOT.parent / "scrapper" / "config" / "profiles.yaml"))
# Ejemplo que se copia a CONFIG_PATH la primera vez (si falta). En la imagen: /srv/defaults/profiles.example.yaml.
CONFIG_EXAMPLE = os.environ.get("INMO_CONFIG_EXAMPLE", str(ROOT.parent / "scrapper" / "config" / "profiles.example.yaml"))
# Usuarios (separados por coma) que pueden lanzar el scrapper; vacío = cualquier usuario autenticado.
RUN_ALLOWED_USERS = {u.strip() for u in os.environ.get("RUN_ALLOWED_USERS", "").split(",") if u.strip()}
# Programador diario interno (lo activa/desactiva el usuario desde la pantalla Estado).
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "40"))
