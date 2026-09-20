import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from inmo.models import make_engine

from . import config
from .models_web import WebBase


def verificar_datos(ruta: str) -> None:
    """Falla con un mensaje claro si no se puede escribir la carpeta de la base (error típico al montar ./data)."""
    p = Path(ruta)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    uid, gid = os.getuid(), os.getgid()
    problemas = []
    if not os.access(p.parent, os.W_OK | os.X_OK):
        problemas.append(f"la carpeta {p.parent} no es escribible")
    if p.exists() and not os.access(p, os.R_OK | os.W_OK):
        problemas.append(f"el archivo {p} no es escribible")
    if problemas:
        raise RuntimeError(
            f"No se puede usar la base de datos: {' y '.join(problemas)} por el usuario del contenedor (UID {uid}:{gid}).\n"
            f"  Solución 1: en el host, dar la carpeta montada a ese usuario:  sudo chown -R {uid}:{gid} ./data\n"
            f"  Solución 2: correr el contenedor con tu propio usuario: PUID=$(id -u) PGID=$(id -g) en el .env\n"
            f"  (Si Docker creó ./data solo, quedó como root: por eso hay que corregirla. Con SELinux, montá el volumen con :Z)")


def init_engine() -> Engine:
    verificar_datos(config.DB_PATH)
    engine = make_engine(config.DB_PATH)  # crea/migra el esquema del scrapper (incluye lat/lng)
    WebBase.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("PRAGMA busy_timeout=5000"))
    return engine
