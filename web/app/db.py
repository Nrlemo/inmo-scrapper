from sqlalchemy import text
from sqlalchemy.engine import Engine

from inmo.models import make_engine

from . import config
from .models_web import WebBase


def init_engine() -> Engine:
    engine = make_engine(config.DB_PATH)  # crea/migra el esquema del scrapper (incluye lat/lng)
    WebBase.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("PRAGMA busy_timeout=5000"))
    return engine
