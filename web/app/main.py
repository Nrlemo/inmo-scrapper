"""Web de revisión de propiedades: FastAPI + Jinja2 + HTMX."""
import csv
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import config, queries, scrapper_ctl
from .auth import User, current_user
from .db import init_engine
from .models_web import BusquedaGuardada, Evento, Revision, Usuario
from .queries import Filtros

ENGINE = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ENGINE
    ENGINE = init_engine()
    scrapper_ctl.marcar_huerfanas(ENGINE)
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=config.ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(config.ROOT / "app" / "templates"))


# ---------- helpers de plantilla ----------
def _money(v, cur="USD"):
    return "–" if v is None else f"{cur or ''} {v:,.0f}".replace(",", ".").strip()


def _fecha(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    return d.strftime("%d/%m/%y") if d else ""


def _ago(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if not d:
        return ""
    s = (datetime.now() - d).total_seconds()
    return "hoy" if s < 86400 else f"hace {int(s // 86400)} d"


def spark(hist: list[dict]) -> Markup:
    """Historial de precios como SVG inline (escalón por cambio)."""
    if len(hist) < 2:
        return Markup("")
    w, h, pad = 300, 70, 8
    vals = [x["precio"] for x in hist]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    t0, t1 = hist[0]["fecha"].timestamp(), max(hist[-1]["fecha"].timestamp(), hist[0]["fecha"].timestamp() + 1)
    pts = [((x["fecha"].timestamp() - t0) / (t1 - t0) * (w - 2 * pad) + pad,
            h - pad - (x["precio"] - lo) / span * (h - 2 * pad)) for x in hist]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3"/>' for x, y in pts)
    return Markup(f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="Historial de precios">'
                  f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="2"/>{dots}</svg>')


templates.env.filters.update(money=_money, fecha=_fecha, ago=_ago)
templates.env.globals["spark"] = spark


# ---------- dependencias ----------
def get_session():
    with Session(ENGINE) as s:
        yield s


def ctx(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Usuario + control de 'última visita' (una sesión = >30 min sin actividad)."""
    if request.method != "GET" and request.headers.get("HX-Request") != "true":
        raise HTTPException(403, "Solicitud no permitida")
    now = datetime.now()
    u = s.get(Usuario, user.username)
    if u is None:
        u = Usuario(username=user.username, email=user.email, ultima_visita=now)
        s.add(u)
        s.commit()
    elif now - u.ultima_visita > timedelta(seconds=60):
        if now - u.ultima_visita > timedelta(minutes=30):
            u.visita_previa = u.ultima_visita
        u.ultima_visita = now
        s.commit()
    return {"user": user, "desde": u.visita_previa, "s": s}


def render(request: Request, name: str, c: dict, **kw):
    conn = c["s"].connection()
    data = {"user": c["user"], "cont": queries.contadores(conn, c["desde"]), **kw}
    return templates.TemplateResponse(request, name, data)


@app.middleware("http")
async def headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src * data:; style-src 'self' 'unsafe-inline'; "
                                            "script-src 'self' 'unsafe-inline'; frame-src https:; frame-ancestors 'self'")
    return r


def _filtros(request: Request, desde) -> Filtros:
    g = request.query_params.get

    def num(k, cast=float):
        try:
            return cast(g(k)) if g(k) else None
        except ValueError:
            return None
    return Filtros(q=(g("q") or "").strip(), barrio=g("barrio") or "", portal=g("portal") or "",
                   inmo=num("inmo", int), pmin=num("pmin"), pmax=num("pmax"), mmin=num("mmin"), amb=num("amb", int),
                   cochera=g("cochera") == "1", estado=g("estado") or "", baja=g("baja") == "1",
                   nuevas=g("nuevas") == "1", inactivas=g("inactivas") == "1", orden=g("orden") or "nuevas",
                   pagina=num("pagina", int) or 1, desde=desde)


# ---------- acciones ----------
ESTADO_CAT = (("descartada", "descartado"), ("contactada", "contactado"), ("favorito", "interesante"))


def _rev(s: Session, pid: int, user: str) -> Revision:
    if s.connection().execute(text("SELECT 1 FROM publicaciones WHERE id=:i"), {"i": pid}).first() is None:
        raise HTTPException(404)
    r = s.get(Revision, pid)
    if r is None:
        r = Revision(publicacion_id=pid, favorito=False, descartada=False, contactada=False, revisada=False,
                     fecha=datetime.now())
        s.add(r)
    r.modificado_por, r.fecha = user, datetime.now()
    return r


def _log(s: Session, pid: int, user: str, tipo: str, detalle: str | None = None):
    s.add(Evento(publicacion_id=pid, usuario=user, tipo=tipo, detalle=detalle, fecha=datetime.now()))


def _sync_estado(s: Session, r: Revision):
    est = next((v for k, v in ESTADO_CAT if getattr(r, k)), "nuevo")
    s.execute(text("UPDATE categorizacion SET estado=:e, fecha_modificacion=:f WHERE publicacion_id=:i"),
              {"e": est, "f": datetime.now(), "i": r.publicacion_id})


ACCIONES = {"favorito", "descartar", "contactada", "restaurar"}


# ---------- pantallas ----------
@app.get("/healthz", include_in_schema=False)
def healthz():
    """Sin autenticación (lo usa el healthcheck de Docker); no expone datos."""
    return Response("ok", media_type="text/plain")


@app.get("/", response_class=HTMLResponse)
def revision(request: Request, despues: int | None = None, c=Depends(ctx)):
    p, total = queries.siguiente_pendiente(c["s"].connection(), despues)
    tpl = "partials/card.html" if request.headers.get("HX-Request") else "revision.html"
    return render(request, tpl, c, p=p, total=total)


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
    elif accion == "descartar":
        r.descartada, r.favorito = True, False
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
        return render(request, "partials/card.html", c, p=p, total=total)
    p = queries.uno(s.connection(), pid)
    return render(request, "partials/detalle.html" if vista == "detalle" else "partials/fila.html", c,
                  p=p, **_detalle_extra(s, p, vista))


def _detalle_extra(s, p, vista):
    if vista != "detalle":
        return {}
    conn = s.connection()
    return {"hist": queries.historial(conn, p["id"]), "hermanas": queries.hermanas(conn, p),
            "eventos": queries.eventos(conn, p["id"])}


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
    s.execute(text("UPDATE categorizacion SET puntaje=:v, fecha_modificacion=:f WHERE publicacion_id=:i"),
              {"v": valor or None, "f": datetime.now(), "i": pid})
    _log(s, pid, user, "puntaje", str(valor))
    s.commit()
    return render(request, "partials/stars.html", c, p=queries.uno(s.connection(), pid))


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
    extra = _detalle_extra(c["s"], p, "detalle")
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
                  inmos=queries.inmobiliarias(conn), guardadas=guardadas)


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
            "puede": not config.RUN_ALLOWED_USERS or c["user"].username in config.RUN_ALLOWED_USERS}


@app.get("/estado", response_class=HTMLResponse)
def estado(request: Request, c=Depends(ctx)):
    from inmo.models import Ejecucion
    conn = c["s"].connection()
    tot = conn.execute(text("SELECT COUNT(*), SUM(activa), SUM(lat IS NOT NULL) FROM publicaciones")).one()
    hist = [scrapper_ctl.vista(e) for e in c["s"].scalars(select(Ejecucion).order_by(Ejecucion.id.desc()).limit(10))]
    return render(request, "estado.html", c, consultas=queries.scrapper_estado(conn), tot=tot, hist=hist,
                  **_panel_scrapper(c))


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
                "favorito", "descartada", "contactada", "puntaje", "notas"])
    for p in rows:
        def safe(v):  # evita inyección de fórmulas al abrir en planilla
            return "'" + v if isinstance(v, str) and v[:1] in "=+-@" else v
        w.writerow([p["id"], p["portal"], p["url"], safe(p["barrio"]), safe(p["direccion"]), p["precio"], p["moneda"],
                    p["m2_cubiertos"], p["ambientes"], round(p["usd_m2"] or 0) or "", p["favorito"], p["descartada"],
                    p["contactada"], p["puntaje"], safe(p["notas"])])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=propiedades.csv"})


@app.exception_handler(HTTPException)
async def http_err(request: Request, exc: HTTPException):
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)
