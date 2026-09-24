"""Configuración por variables de entorno."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# El paquete `inmo` (carpeta scrapper/) aporta los modelos de la SQLite, los parsers y la lógica de la ronda.
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
SESSION_REMEMBER_DAYS = float(os.environ.get("SESSION_REMEMBER_DAYS", "90"))  # «Mantener sesión»: sin cierre por inactividad
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").strip().lower()   # auto (según https) | true | false
# Secreto opcional compartido con el proxy (cabecera X-Proxy-Secret) como defensa extra.
PROXY_SECRET = os.environ.get("PROXY_SECRET")
# Perfiles y zonas de búsqueda que recorre la ronda por navegador (también los usa `python -m inmo retag`).
CONFIG_PATH = os.environ.get("INMO_CONFIG", str(ROOT.parent / "scrapper" / "config" / "profiles.yaml"))
# Ejemplo que se copia a CONFIG_PATH la primera vez (si falta). En la imagen: /srv/defaults/profiles.example.yaml.
CONFIG_EXAMPLE = os.environ.get("INMO_CONFIG_EXAMPLE", str(ROOT.parent / "scrapper" / "config" / "profiles.example.yaml"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "40"))
# Backups automáticos de la SQLite (app/backups.py): una vez por día a BACKUP_HORA, en BACKUP_DIR (por defecto la
# carpeta `backups` junto a la base) y, si se define, una segunda copia en BACKUP_DIR_EXTRA (otro disco u otra máquina).
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
BACKUP_HORA = os.environ.get("BACKUP_HORA", "05:00").strip()
if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", BACKUP_HORA):
    raise RuntimeError(f"BACKUP_HORA inválida: {BACKUP_HORA!r} (usar HH:MM, por ejemplo 05:00)")
BACKUP_DIR = os.environ.get("BACKUP_DIR") or str(Path(DB_PATH).parent / "backups")
BACKUP_DIR_EXTRA = os.environ.get("BACKUP_DIR_EXTRA", "").strip() or None
# Retención: los más nuevos de cada uno de los últimos N días, semanas y meses
BACKUP_DIARIOS = int(os.environ.get("BACKUP_DIARIOS", "7"))
BACKUP_SEMANALES = int(os.environ.get("BACKUP_SEMANALES", "4"))
BACKUP_MENSUALES = int(os.environ.get("BACKUP_MENSUALES", "6"))
