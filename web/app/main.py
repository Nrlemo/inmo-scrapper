"""Web de revisión de propiedades: FastAPI + Jinja2 + HTMX."""
import csv
import io
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import quote
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import config, queries, scheduler, scrapper_ctl
from . import auth
from .auth import LoginRequired, PasswordChangeRequired, SetupRequired
from .auth_routes import router as auth_router
from .navegador_routes import router as navegador_router, token_de
from .core import ctx, headers, render, templates
from .db import init_engine, sembrar_config
from .models_web import BusquedaGuardada, Evento, Puntaje, Revision, Usuario
from .queries import Filtros

ENGINE = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ENGINE
    sembrar_config()
    ENGINE = app.state.engine = init_engine()
    scrapper_ctl.marcar_huerfanas(ENGINE)
    log = logging.getLogger("uvicorn.error")
    if config.AUTH_MODE == "none":
        log.warning("AUTENTICACIÓN DESACTIVADA (AUTH_MODE=none): cualquiera que llegue a esta URL puede ver y modificar todo.")
    elif config.AUTH_MODE == "basic":
        auth.avisar_instalacion(ENGINE)
    stop = threading.Event()
    if config.SCHEDULER_ENABLED:
        threading.Thread(target=scheduler.run_forever, args=(ENGINE, stop), daemon=True, name="scheduler").start()
    yield
    stop.set()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(headers)
app.include_router(auth_router)
app.include_router(navegador_router)
app.mount("/static", StaticFiles(directory=config.ROOT / "app" / "static"), name="static")
def _filtros(request: Request, desde) -> Filtros:
    g = request.query_params.get

    def num(k, cast=float):
        try:
            return cast(g(k)) if g(k) else None
        except ValueError:
            return None
    return Filtros(q=(g("q") or "").strip(), barrio=g("barrio") or "", portal=g("portal") or "",
                   inmo=num("inmo", int), pmin=num("pmin"), pmax=num("pmax"), mmin=num("mmin"), amb=num("amb", int),
                   cochera=g("cochera") == "1", etiqueta=(g("etiqueta") or "").strip(), estado=g("estado") or "", baja=g("baja") == "1",
                   nuevas=g("nuevas") == "1", inactivas=g("inactivas") == "1", orden=g("orden") or "nuevas",
                   pagina=num("pagina", int) or 1, desde=desde)


# ---------- acciones ----------
ESTADO_CAT = (("descartada", "descartado"), ("contactada", "contactado"), ("favorito", "interesante"), ("potencial", "interesante"))


def _rev(s: Session, pid: int, user: str) -> Revision:
    if s.connection().execute(text("SELECT 1 FROM publicaciones WHERE id=:i"), {"i": pid}).first() is None:
        raise HTTPException(404)
    r = s.get(Revision, pid)
    if r is None:
        r = Revision(publicacion_id=pid, favorito=False, potencial=False, descartada=False, contactada=False,
                     revisada=False, fecha=datetime.now())
        s.add(r)
    r.modificado_por, r.fecha = user, datetime.now()
    return r


def _log(s: Session, pid: int, user: str, tipo: str, detalle: str | None = None):
    s.add(Evento(publicacion_id=pid, usuario=user, tipo=tipo, detalle=detalle, fecha=datetime.now()))


def _sync_estado(s: Session, r: Revision):
    est = next((v for k, v in ESTADO_CAT if getattr(r, k)), "nuevo")
    s.execute(text("UPDATE categorizacion SET estado=:e, fecha_modificacion=:f WHERE publicacion_id=:i"),
              {"e": est, "f": datetime.now(), "i": r.publicacion_id})


ACCIONES = {"favorito", "potencial", "descartar", "contactada", "restaurar"}


# ---------- pantallas ----------
@app.get("/healthz", include_in_schema=False)
def healthz():
    """Sin autenticación (lo usa el healthcheck de Docker); no expone datos."""
    return Response("ok", media_type="text/plain")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Se sirve desde la raíz (no /static/) para poder controlar todo el sitio (scope /)."""
    return FileResponse(config.ROOT / "app" / "static" / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def revision(request: Request, despues: int | None = None, c=Depends(ctx)):
    conn = c["s"].connection()
    p, total = queries.siguiente_pendiente(conn, despues)
    tpl = "partials/card.html" if request.headers.get("HX-Request") else "revision.html"
    return render(request, tpl, c, p=p, total=total, hoy=queries.revisadas_hoy(conn, c["user"].username))


@app.post("/p/{pid}/accion", response_class=HTMLResponse)
def accion(request: Request, pid: int, accion: str = Form(...), vista: str = Form("fila"), c=Depends(ctx)):
    if accion not in ACCIONES:
        raise HTTPException(400)
    s, user = c["s"], c["user"].username
    r = _rev(s, pid, user)
    if accion == "favorito":
        r.favorito = not r.favorito
        if r.favorito:
            r.descartada = False
    elif accion == "potencial":
        r.potencial = not r.potencial
        if r.potencial:
            r.descartada = False
    elif accion == "descartar":
        r.descartada, r.favorito, r.potencial = True, False, False
    elif accion == "contactada":
        r.contactada = not r.contactada
        r.fecha_contacto = datetime.now() if r.contactada else None
    elif accion == "restaurar":
        r.descartada, r.revisada = False, False
    if accion != "restaurar":
        r.revisada = True
    _sync_estado(s, r)
    _log(s, pid, user, accion, "on" if getattr(r, {"descartar": "descartada"}.get(accion, accion), False) else "off")
    s.commit()
    if vista == "card":  # en la cola de revisión: pasar a la siguiente
        p, total = queries.siguiente_pendiente(s.connection(), pid)
        if p and p["id"] == pid:
            p = None
        return render(request, "partials/card.html", c, p=p, total=total, hoy=queries.revisadas_hoy(s.connection(), user))
    p = queries.uno(s.connection(), pid)
    return render(request, "partials/detalle.html" if vista == "detalle" else "partials/fila.html", c,
                  p=p, **_detalle_extra(s, p, vista, user))


def _detalle_extra(s, p, vista, user: str):
    if vista != "detalle":
        return {}
    conn = s.connection()
    return {"hist": queries.historial(conn, p["id"]), "hermanas": queries.hermanas(conn, p),
            "eventos": queries.eventos(conn, p["id"]), **_puntajes_ctx(conn, p["id"], user)}


def _puntajes_ctx(conn, pid: int, user: str) -> dict:
    return {"mio": queries.mi_puntaje(conn, pid, user), "puntajes": queries.puntajes(conn, pid)}


@app.post("/p/{pid}/notas")
def notas(pid: int, notas: str = Form(""), c=Depends(ctx)):
    s, user = c["s"], c["user"].username
    r = _rev(s, pid, user)
    r.notas = notas.strip()[:4000] or None
    _log(s, pid, user, "notas")
    s.commit()
    return Response(status_code=204)


@app.post("/p/{pid}/puntaje", response_class=HTMLResponse)
def puntaje(request: Request, pid: int, valor: int = Form(...), c=Depends(ctx)):
    if not 0 <= valor <= 5:
        raise HTTPException(400)
    s, user = c["s"], c["user"].username
    _rev(s, pid, user)
    mio = s.get(Puntaje, (pid, user))
    if valor:
        if mio is None:
            mio = Puntaje(publicacion_id=pid, usuario=user)
            s.add(mio)
        mio.puntaje, mio.fecha = valor, datetime.now()
    elif mio is not None:
        s.delete(mio)
    s.flush()
    # categorizacion.puntaje queda como el promedio redondeado de todos los usuarios
    s.execute(text("UPDATE categorizacion SET puntaje=(SELECT ROUND(AVG(puntaje)) FROM web_puntajes WHERE publicacion_id=:i), "
                   "fecha_modificacion=:f WHERE publicacion_id=:i"), {"f": datetime.now(), "i": pid})
    _log(s, pid, user, "puntaje", str(valor))
    s.commit()
    conn = s.connection()
    return render(request, "partials/stars.html", c, p=queries.uno(conn, pid), **_puntajes_ctx(conn, pid, user))


@app.post("/p/{pid}/etiquetas")
def etiquetas(pid: int, etiquetas: str = Form(""), c=Depends(ctx)):
    s, user = c["s"], c["user"].username
    _rev(s, pid, user)
    tags = sorted({t.strip().lower()[:30] for t in etiquetas.split(",") if t.strip()})[:12]
    s.execute(text("UPDATE categorizacion SET etiquetas=:t, fecha_modificacion=:f WHERE publicacion_id=:i"),
              {"t": json.dumps(tags), "f": datetime.now(), "i": pid})
    _log(s, pid, user, "etiquetas", ",".join(tags))
    s.commit()
    return Response(status_code=204)


@app.get("/p/{pid}", response_class=HTMLResponse)
def detalle(request: Request, pid: int, panel: int = 0, c=Depends(ctx)):
    p = queries.uno(c["s"].connection(), pid)
    if not p:
        raise HTTPException(404)
    extra = _detalle_extra(c["s"], p, "detalle", c["user"].username)
    return render(request, "partials/detalle.html" if panel else "detalle.html", c, p=p, vista="detalle", **extra)


def _lista_ctx(request: Request, c):
    f = _filtros(request, c["desde"])
    rows, total = queries.listar(c["s"].connection(), f, config.PAGE_SIZE)
    qs = {k: v for k, v in request.query_params.items() if k != "pagina" and v}
    return {"f": f, "rows": rows, "total": total, "qs": urlencode(qs),
            "paginas": max(1, -(-total // config.PAGE_SIZE))}


@app.get("/lista", response_class=HTMLResponse)
def lista(request: Request, c=Depends(ctx)):
    d = _lista_ctx(request, c)
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "resultados":
        return render(request, "partials/resultados.html", c, **d)
    conn = c["s"].connection()
    guardadas = [dict(r) for r in conn.execute(text("SELECT id, nombre, querystring FROM web_busquedas ORDER BY nombre")).mappings()]
    return render(request, "lista.html", c, **d, barrios=queries.barrios(conn), portales=queries.portales(conn),
                  inmos=queries.inmobiliarias(conn), etiquetas=queries.etiquetas(conn), guardadas=guardadas)


@app.post("/busquedas", response_class=HTMLResponse)
def guardar_busqueda(nombre: str = Form(...), querystring: str = Form(""), c=Depends(ctx)):
    s = c["s"]
    s.add(BusquedaGuardada(nombre=nombre.strip()[:80] or "Búsqueda", querystring=querystring, usuario=c["user"].username))
    s.commit()
    return Response(headers={"HX-Redirect": "/lista?" + querystring})


@app.post("/busquedas/{bid}/borrar")
def borrar_busqueda(bid: int, c=Depends(ctx)):
    c["s"].execute(text("DELETE FROM web_busquedas WHERE id=:i"), {"i": bid})
    c["s"].commit()
    return Response(headers={"HX-Redirect": "/lista"})


@app.get("/ranking", response_class=HTMLResponse)
def ranking(request: Request, usuario: str = "", inactivas: int = 0, descartadas: int = 0, c=Depends(ctx)):
    rows, usuarios = queries.ranking(c["s"].connection(), usuario, bool(inactivas), bool(descartadas))
    return render(request, "ranking.html", c, rows=rows, usuarios=usuarios, sel=usuario,
                  inactivas=inactivas, descartadas=descartadas)


@app.get("/cambios", response_class=HTMLResponse)
def cambios(request: Request, dias: int = 30, favoritas: int = 0, c=Depends(ctx)):
    rows = queries.cambios(c["s"].connection(), min(max(dias, 1), 365), bool(favoritas))
    return render(request, "cambios.html", c, rows=rows, dias=dias, favoritas=favoritas)


@app.get("/comparar", response_class=HTMLResponse)
def comparar(request: Request, ids: str = "", c=Depends(ctx)):
    conn = c["s"].connection()
    lista_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()][:6]
    usando_favoritas = not lista_ids
    if usando_favoritas:
        lista_ids = [r[0] for r in conn.execute(text(
            "SELECT publicacion_id FROM web_revision WHERE favorito=1 ORDER BY fecha DESC LIMIT 6"))]
    props = queries.varios(conn, lista_ids)
    for p in props:
        p["hist"] = queries.historial(conn, p["id"])
    stats = queries.stats_barrios(conn)
    med = {b["barrio"]: b["mediana"] for b in stats}
    return render(request, "comparar.html", c, props=props, stats=stats, med=med, favoritas=usando_favoritas)


@app.get("/mapa", response_class=HTMLResponse)
def mapa(request: Request, c=Depends(ctx)):
    n = c["s"].connection().execute(text("SELECT COUNT(*) FROM publicaciones WHERE lat IS NOT NULL AND activa=1")).scalar_one()
    return render(request, "mapa.html", c, con_coords=n)


@app.get("/api/mapa")
def api_mapa(request: Request, c=Depends(ctx)):
    f = _filtros(request, c["desde"])
    return JSONResponse(queries.mapa(c["s"].connection(), f))


def _panel_scrapper(c) -> dict:
    """Contexto del panel de ejecución: corrida activa (o la última) + formulario."""
    from inmo.models import Ejecucion
    s = c["s"]
    act = scrapper_ctl.activa(s)
    ult = s.scalar(select(Ejecucion).order_by(Ejecucion.id.desc()).limit(1))
    e = act or ult
    return {"ej": scrapper_ctl.vista(e) if e else None, "activa": act is not None,
            "log": scrapper_ctl.cola_log(e.id) if e else [], "zonas_por_portal": scrapper_ctl.portales_y_zonas(),
            "puede": not config.RUN_ALLOWED_USERS or c["user"].username in config.RUN_ALLOWED_USERS,
            "prog": scheduler.obtener(s)}


@app.get("/estado", response_class=HTMLResponse)
def estado(request: Request, c=Depends(ctx)):
    from inmo.models import Ejecucion
    conn = c["s"].connection()
    tot = conn.execute(text("SELECT COUNT(*), SUM(activa), SUM(lat IS NOT NULL) FROM publicaciones")).one()
    hist = [scrapper_ctl.vista(e) for e in c["s"].scalars(select(Ejecucion).order_by(Ejecucion.id.desc()).limit(10))]
    return render(request, "estado.html", c, consultas=queries.scrapper_estado(conn), tot=tot, hist=hist,
                  tok=token_de(c["s"], c["user"].username), **_panel_scrapper(c))


@app.get("/scrapper/estado", response_class=HTMLResponse)
def scrapper_estado(request: Request, run: int = 0, c=Depends(ctx)):
    """Panel de progreso (lo consulta HTMX cada pocos segundos mientras corre)."""
    ctxd = _panel_scrapper(c)
    resp = render(request, "partials/scrapper.html", c, **ctxd)
    if run and not ctxd["activa"]:
        resp.headers["HX-Refresh"] = "true"  # terminó: recargar para ver el historial actualizado
    return resp


@app.post("/scrapper/iniciar", response_class=HTMLResponse)
def scrapper_iniciar(request: Request, portal: str = Form(...), zonas: list[str] = Form([]),
                     skip_gap: str = Form(""), c=Depends(ctx)):
    if config.RUN_ALLOWED_USERS and c["user"].username not in config.RUN_ALLOWED_USERS:
        raise HTTPException(403, "No tenés permiso para ejecutar el scrapper")
    error = None
    try:
        scrapper_ctl.iniciar(ENGINE, c["user"].username, portal, zonas, skip_gap == "1")
    except scrapper_ctl.Ocupado:
        error = "Ya hay una corrida en curso."
    except ValueError as e:
        error = str(e)
    c["s"].expire_all()
    return render(request, "partials/scrapper.html", c, error=error, **_panel_scrapper(c))


@app.post("/scrapper/programacion", response_class=HTMLResponse)
def scrapper_programacion(request: Request, activa: str = Form(""), hora: str = Form("03:00"), c=Depends(ctx)):
    if config.RUN_ALLOWED_USERS and c["user"].username not in config.RUN_ALLOWED_USERS:
        raise HTTPException(403, "No tenés permiso para cambiar la programación")
    error = None
    try:
        scheduler.configurar(c["s"], activa == "1", hora.strip(), c["user"].username)
    except ValueError as e:
        error = str(e)
    c["s"].expire_all()
    return render(request, "partials/programacion.html", c, error_prog=error, **_panel_scrapper(c))


@app.post("/scrapper/cancelar", response_class=HTMLResponse)
def scrapper_cancelar(request: Request, c=Depends(ctx)):
    if config.RUN_ALLOWED_USERS and c["user"].username not in config.RUN_ALLOWED_USERS:
        raise HTTPException(403)
    act = scrapper_ctl.activa(c["s"])
    if act:
        scrapper_ctl.cancelar(ENGINE, act.id)
        c["s"].expire_all()
    return render(request, "partials/scrapper.html", c, **_panel_scrapper(c))


@app.get("/export.csv")
def export(request: Request, c=Depends(ctx)):
    f = _filtros(request, c["desde"])
    rows, _ = queries.listar(c["s"].connection(), f, 100000)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "portal", "url", "barrio", "direccion", "precio", "moneda", "m2_cub", "ambientes", "usd_m2",
                "favorito", "potencial", "descartada", "contactada", "puntaje_promedio", "votos", "notas"])
    for p in rows:
        def safe(v):  # evita inyección de fórmulas al abrir en planilla
            return "'" + v if isinstance(v, str) and v[:1] in "=+-@" else v
        w.writerow([p["id"], p["portal"], p["url"], safe(p["barrio"]), safe(p["direccion"]), p["precio"], p["moneda"],
                    p["m2_cubiertos"], p["ambientes"], round(p["usd_m2"] or 0) or "", p["favorito"], p["potencial"],
                    p["descartada"], p["contactada"], round(p["puntaje"], 2) if p["puntaje"] else "", p["votos"],
                    safe(p["notas"])])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=propiedades.csv"})


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    if _hx(request):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "no autenticado"}, status_code=401)
    destino = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    nxt = f"?next={quote(destino, safe='')}" if request.method == "GET" and request.url.path != "/" else ""
    return RedirectResponse("/login" + nxt, status_code=303)


@app.exception_handler(SetupRequired)
async def _setup_required(request: Request, exc: SetupRequired):
    if _hx(request):
        return Response(status_code=401, headers={"HX-Redirect": "/setup"})
    return RedirectResponse("/setup", status_code=303)


@app.exception_handler(PasswordChangeRequired)
async def _must_change(request: Request, exc: PasswordChangeRequired):
    if _hx(request):
        return Response(status_code=403, headers={"HX-Redirect": "/cuenta"})
    return RedirectResponse("/cuenta", status_code=303)


TITULOS = {401: "Acceso no autorizado", 403: "Acceso denegado", 404: "No encontrado", 405: "Método no permitido"}


@app.exception_handler(StarletteHTTPException)
async def http_err(request: Request, exc: StarletteHTTPException):
    """Página de error simple. Los errores de autenticación/permisos (401/403) llevan el GIF."""
    code = exc.status_code
    if request.url.path.startswith("/api/"):   # la app Android (y /api/mapa) esperan JSON, no la página de error
        return JSONResponse({"detail": str(exc.detail)}, status_code=code, headers=getattr(exc, "headers", None))
    return templates.TemplateResponse(
        request, "error.html",
        {"titulo": TITULOS.get(code, f"Error {code}"), "detalle": str(exc.detail), "gif": code in (401, 403)},
        status_code=code, headers=getattr(exc, "headers", None))
