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
# Sólo para desarrollo local sin proxy: si está definida, se usa como usuario cuando falta la cabecera.
DEV_USER = os.environ.get("AUTH_DEV_USER")
# Secreto opcional compartido con el proxy (cabecera X-Proxy-Secret) como defensa extra.
PROXY_SECRET = os.environ.get("PROXY_SECRET")
# Configuración de perfiles/zonas del scrapper (la misma que usa el CLI).
CONFIG_PATH = os.environ.get("INMO_CONFIG", str(ROOT.parent / "scrapper" / "config" / "profiles.yaml"))
# Usuarios (separados por coma) que pueden lanzar el scrapper; vacío = cualquier usuario autenticado.
RUN_ALLOWED_USERS = {u.strip() for u in os.environ.get("RUN_ALLOWED_USERS", "").split(",") if u.strip()}
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "40"))
