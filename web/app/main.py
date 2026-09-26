"""Web de revisión de propiedades: FastAPI + Jinja2 + HTMX. Arma la app: arranque, routers por área y errores."""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import auth, backups, config, errores, geocodificacion, rondas
from .auth_routes import router as auth_router
from .backup_routes import router as backup_router
from .busqueda_routes import router as busqueda_router
from .core import headers
from .db import init_engine, sembrar_config
from .estado_routes import router as estado_router
from .lista_routes import router as lista_router
from .navegador_routes import router as navegador_router
from .revision_routes import router as revision_router

ENGINE = None     # lo usan los tests (from app.main import ENGINE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ENGINE
    sembrar_config()
    ENGINE = app.state.engine = init_engine()
    with Session(ENGINE) as s:
        rondas.marcar_huerfanas(s)
    log = logging.getLogger("uvicorn.error")
    if config.AUTH_MODE == "none":
        log.warning("AUTENTICACIÓN DESACTIVADA (AUTH_MODE=none): cualquiera que llegue a esta URL puede ver y modificar todo.")
    elif config.AUTH_MODE == "basic":
        auth.avisar_instalacion(ENGINE)
    stop = threading.Event()
    if config.BACKUP_ENABLED:
        threading.Thread(target=backups.run_forever, args=(stop,), daemon=True, name="backups").start()
        log.info("Backups diarios a las %s en %s%s", config.BACKUP_HORA, config.BACKUP_DIR,
                 f" (copia extra en {config.BACKUP_DIR_EXTRA})" if config.BACKUP_DIR_EXTRA else "")
    if config.GEOCODIFICAR:
        threading.Thread(target=geocodificacion.run_forever, args=(ENGINE, stop), daemon=True, name="geo").start()
    yield
    stop.set()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(headers)
for r in (revision_router, lista_router, estado_router, auth_router, navegador_router, busqueda_router, backup_router):
    app.include_router(r)
app.mount("/static", StaticFiles(directory=config.ROOT / "app" / "static"), name="static")
errores.registrar(app)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Sin autenticación (lo usa el healthcheck de Docker); no expone datos."""
    return Response("ok", media_type="text/plain")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Se sirve desde la raíz (no /static/) para poder controlar todo el sitio (scope /)."""
    return FileResponse(config.ROOT / "app" / "static" / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})
