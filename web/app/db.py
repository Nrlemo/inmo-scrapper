import logging
import os
import shutil
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from inmo.models import make_engine

from . import config
from .models_web import AuthBase, Cuenta, Sesion, TokenApi, WebBase


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


def _motor_auth(ruta: str) -> Engine:
    """Motor de la base de autenticación (sólo las tablas de AuthBase, sin el esquema de avisos)."""
    engine = create_engine(f"sqlite:///{ruta}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    AuthBase.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("PRAGMA busy_timeout=5000"))
        if "base" not in {r[1] for r in c.execute(text("PRAGMA table_info(web_sesiones)"))}:
            c.execute(text("ALTER TABLE web_sesiones ADD COLUMN base VARCHAR(8) NOT NULL DEFAULT 'compra'"))
    return engine


def _motor_avisos(ruta: str) -> Engine:
    """Motor de una base de avisos (compra o alquiler): esquema del scrapper + tablas de la web."""
    verificar_datos(ruta)
    engine = make_engine(ruta)  # crea/migra el esquema del scrapper (incluye lat/lng)
    with engine.connect() as c:
        habia_puntajes = c.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_puntajes'")).first()
    WebBase.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("PRAGMA busy_timeout=5000"))
        _migrar(c, primera_vez_puntajes=habia_puntajes is None)
    return engine


def migrar_auth(auth: Engine, compra: Engine) -> dict[str, int]:
    """Antes de las bases separadas, cuentas, sesiones y tokens vivían en la base de compra. Una sola vez (marcado con
    `PRAGMA user_version` de la base de autenticación) las COPIA a la base de autenticación, con sus ids. No toca la
    base de compra: las tablas viejas quedan ahí, así que volver a una versión anterior sigue andando. Devuelve
    cuántas filas copió por tabla ({} si no había nada que hacer)."""
    with auth.connect() as c:
        if c.execute(text("PRAGMA user_version")).scalar_one() >= 1:
            return {}
    copiadas: dict[str, int] = {}
    with compra.connect() as origen, auth.begin() as destino:
        for tabla in (Cuenta.__table__, Sesion.__table__, TokenApi.__table__):
            viejas = [r[1] for r in origen.execute(text(f"PRAGMA table_info({tabla.name})"))]
            cols = [c.name for c in tabla.columns if c.name in viejas]
            if not cols:
                continue
            # Las columnas que la tabla vieja no tenía (p. ej. `base`) toman su valor por defecto. Se copia con SQL
            # directo: las fechas pasan tal cual, sin convertirlas a datetime y volver.
            extra = {c.name: c.default.arg for c in tabla.columns
                     if c.name not in viejas and c.default is not None and c.default.is_scalar}
            nombres = cols + list(extra)
            filas = [{**dict(r), **extra} for r in origen.execute(text(f"SELECT {', '.join(cols)} FROM {tabla.name}")).mappings()]
            if filas:
                destino.execute(text(f"INSERT INTO {tabla.name} ({', '.join(nombres)}) "
                                     f"VALUES ({', '.join(':' + n for n in nombres)})"), filas)
            copiadas[tabla.name] = len(filas)
        destino.execute(text("PRAGMA user_version = 1"))
    if any(copiadas.values()):
        logging.getLogger("uvicorn.error").info(
            "Cuentas, sesiones y tokens copiados a la base de autenticación: %s", copiadas)
    return copiadas


def init_engines() -> dict[str, Engine]:
    """{"compra", "alquiler", "auth"}: una SQLite por cada una."""
    compra = _motor_avisos(config.DB_PATHS["compra"])
    alquiler = _motor_avisos(config.DB_PATHS["alquiler"])
    verificar_datos(config.DB_PATH_AUTH)
    auth = _motor_auth(config.DB_PATH_AUTH)
    migrar_auth(auth, compra)
    return {"compra": compra, "alquiler": alquiler, "auth": auth}


def _migrar(c, primera_vez_puntajes: bool) -> None:
    """create_all no altera tablas existentes: agrega columnas nuevas y pasa datos al esquema nuevo."""
    rev = {r[1] for r in c.execute(text("PRAGMA table_info(web_revision)"))}
    if "potencial" not in rev:
        c.execute(text("ALTER TABLE web_revision ADD COLUMN potencial BOOLEAN NOT NULL DEFAULT 0"))
    # El programador diario del scrapper HTTP ya no existe (la extensión programa su propia ronda).
    c.execute(text("DROP TABLE IF EXISTS web_programacion"))
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
