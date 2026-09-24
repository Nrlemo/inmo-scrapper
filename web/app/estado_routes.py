"""Estado: resumen, ronda en curso (progreso y cancelar) y los paneles de búsqueda, extensión y backups."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text

from . import queries, rondas
from .backup_routes import panel_ctx as backups_ctx
from .busqueda_routes import panel_ctx as busqueda_ctx
from .core import ctx, render
from .navegador_routes import token_de
from inmo.models import Ejecucion   # después de `config` (vía core), que agrega el paquete del scrapper al path

router = APIRouter()


def _panel_ronda(c) -> dict:
    """Contexto del panel de la ronda: la que está en curso (o la última)."""
    s = c["s"]
    act = rondas.activa(s)
    e = act or s.scalar(select(Ejecucion).order_by(Ejecucion.id.desc()).limit(1))
    return {"ej": rondas.vista(e) if e else None, "activa": act is not None,
            "zonas_por_portal": rondas.portales_y_zonas(s)}


@router.get("/estado", response_class=HTMLResponse)
def estado(request: Request, c=Depends(ctx)):
    conn = c["s"].connection()
    tot = conn.execute(text("SELECT COUNT(*), SUM(activa), SUM(lat IS NOT NULL), SUM(activa AND fuera_filtro) FROM publicaciones")).one()
    hist = [rondas.vista(e) for e in c["s"].scalars(select(Ejecucion).order_by(Ejecucion.id.desc()).limit(10))]
    return render(request, "estado.html", c, consultas=queries.consultas(conn), tot=tot, hist=hist,
                  tok=token_de(c["s"], c["user"].username), **_panel_ronda(c), **busqueda_ctx(c["s"], c["user"]),
                  **backups_ctx(c["user"]))


@router.get("/ronda/estado", response_class=HTMLResponse)
def ronda_estado(request: Request, run: int = 0, c=Depends(ctx)):
    """Panel de progreso (lo consulta HTMX cada pocos segundos mientras corre)."""
    ctxd = _panel_ronda(c)
    resp = render(request, "partials/ronda.html", c, **ctxd)
    if run and not ctxd["activa"]:
        resp.headers["HX-Refresh"] = "true"  # terminó: recargar para ver el historial actualizado
    return resp


@router.post("/ronda/cancelar", response_class=HTMLResponse)
def ronda_cancelar(request: Request, c=Depends(ctx)):
    act = rondas.activa(c["s"])
    if act:
        rondas.cancelar(c["s"], act.id)
        c["s"].expire_all()
    return render(request, "partials/ronda.html", c, **_panel_ronda(c))
