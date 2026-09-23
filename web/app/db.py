import logging
import os
import shutil
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


def sembrar_config() -> bool:
    """Si no existe profiles.yaml, lo crea copiando el ejemplo. Devuelve True si lo creó. Nunca pisa uno existente."""
    log = logging.getLogger("uvicorn.error")
    destino, origen = Path(config.CONFIG_PATH), Path(config.CONFIG_EXAMPLE)
    if destino.exists() or not origen.is_file():
        return False
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origen, destino)
    except OSError as e:
        log.warning("No se pudo crear %s a partir del ejemplo (%s). Si /config está montado como solo lectura o sin "
                    "permisos, copiá profiles.example.yaml a mano.", destino, e)
        return False
    log.info("Se creó %s a partir del ejemplo: editalo con tus zonas y presupuesto.", destino)
    return True


def init_engine() -> Engine:
    verificar_datos(config.DB_PATH)
    engine = make_engine(config.DB_PATH)  # crea/migra el esquema del scrapper (incluye lat/lng)
    with engine.connect() as c:
        habia_puntajes = c.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_puntajes'")).first()
    WebBase.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("PRAGMA busy_timeout=5000"))
        _migrar(c, primera_vez_puntajes=habia_puntajes is None)
    return engine


def _migrar(c, primera_vez_puntajes: bool) -> None:
    """create_all no altera tablas existentes: agrega columnas nuevas y pasa datos al esquema nuevo."""
    rev = {r[1] for r in c.execute(text("PRAGMA table_info(web_revision)"))}
    if "potencial" not in rev:
        c.execute(text("ALTER TABLE web_revision ADD COLUMN potencial BOOLEAN NOT NULL DEFAULT 0"))
    if primera_vez_puntajes:
        # Antes el puntaje era uno solo por publicación: se le atribuye a quien lo puso por última vez
        # (según el registro de actividad) o, si no hay registro, a quien modificó la publicación.
        c.execute(text("""
            INSERT INTO web_puntajes (publicacion_id, usuario, puntaje, fecha)
            SELECT c.publicacion_id,
                   COALESCE((SELECT e.usuario FROM web_eventos e WHERE e.publicacion_id = c.publicacion_id
                             AND e.tipo = 'puntaje' ORDER BY e.id DESC LIMIT 1),
                            (SELECT r.modificado_por FROM web_revision r WHERE r.publicacion_id = c.publicacion_id),
                            'anterior'),
                   c.puntaje, c.fecha_modificacion
            FROM categorizacion c WHERE c.puntaje BETWEEN 1 AND 5"""))
