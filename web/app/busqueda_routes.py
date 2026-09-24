"""Pantalla Estado → Búsqueda: editar los filtros que recorre la ronda y que deciden qué avisos se muestran."""
import copy
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from inmo import filtros
from inmo.connectors.zonaprop import search_urls

from . import busqueda
from .core import ctx, puede_administrar as puede_editar, render

router = APIRouter()


def panel_ctx(s, user, sel: int = 0, doc: dict | None = None, **extra) -> dict:
    """Contexto de partials/busqueda.html. `doc` permite mostrar un formulario no guardado (con error)."""
    doc = doc or busqueda.obtener(s)
    sel = min(max(sel, 0), len(doc["busquedas"]) - 1)
    b = doc["busquedas"][sel]
    urls = {}
    for p in filtros.perfiles({"busquedas": [b]}):
        portal = next(iter(p["portals"]))
        try:
            urls[portal] = search_urls(p)
        except (KeyError, ValueError, IndexError):   # plantilla con placeholders desconocidos
            urls[portal] = []
    fila = busqueda.info(s)
    return {"bdoc": doc, "bsel": sel, "b": b, "burls": urls, "bpuede": puede_editar(user), "bfila": fila,
            "PORTALES": filtros.PORTALES, "DISPONIBLES": filtros.DISPONIBLES, "zonas_a_texto": filtros.zonas_a_texto,
            **extra}


def _verificar(c):
    if not puede_editar(c["user"]):
        raise HTTPException(403, "Sólo un administrador puede cambiar los filtros de búsqueda")


def _responder(request: Request, c, sel: int, doc: dict | None = None, **extra):
    return render(request, "partials/busqueda.html", c, **panel_ctx(c["s"], c["user"], sel, doc, **extra))


@router.get("/busqueda", response_class=HTMLResponse)
def ver(request: Request, b: int = 0, c=Depends(ctx)):
    return _responder(request, c, b)


def _desde_form(b: dict, form) -> dict:
    """Aplica el formulario sobre la búsqueda `b`. Los portales que no vienen en el formulario (etapa 2) no cambian."""
    b = copy.deepcopy(b)
    g = lambda k: (form.get(k) or "").strip()  # noqa: E731
    b["nombre"] = g("nombre")
    com = b["comunes"]
    for k in filtros.NUMERICOS:
        com[k] = g(k) or None
    com["apto_credito"] = form.get("apto_credito") == "1"
    com["excluir"] = [x.strip() for x in re.split(r"[,\n]", form.get("excluir") or "") if x.strip()]
    for portal in filtros.DISPONIBLES:
        if f"{portal}_zonas" not in form:
            continue
        pc = b["portales"].setdefault(portal, {"zonas": [], "ajustes": {}, "plantilla": None})
        pc["activo"] = form.get(f"{portal}_activo") == "1"
        pc["zonas"] = filtros.zonas_desde_texto(form.get(f"{portal}_zonas") or "")
        pc["ajustes"] = {k: g(f"{portal}_{k}") for k in filtros.AJUSTABLES if g(f"{portal}_{k}")}
        pc["plantilla"] = g(f"{portal}_plantilla") or None
    return b


@router.post("/busqueda/{idx}", response_class=HTMLResponse)
async def guardar(request: Request, idx: int, c=Depends(ctx)):
    _verificar(c)
    s, doc = c["s"], busqueda.obtener(c["s"])
    if not 0 <= idx < len(doc["busquedas"]):
        raise HTTPException(404)
    form = await request.form()
    try:
        doc["busquedas"][idx] = _desde_form(doc["busquedas"][idx], form)
        fuera = busqueda.guardar(s, doc, c["user"].username)
    except ValueError as e:
        s.rollback()
        return _responder(request, c, idx, berror=str(e), bform=dict(form))   # se redibuja con lo que se escribió
    return _responder(request, c, idx, bok=f"Guardado. {fuera} " + ("aviso activo queda" if fuera == 1 else "avisos activos quedan")
                                            + " fuera de los filtros (ocultos; los que marcaste se siguen viendo).")


@router.post("/busqueda/{idx}/copiar", response_class=HTMLResponse)
def copiar(request: Request, idx: int, c=Depends(ctx)):
    _verificar(c)
    doc = busqueda.obtener(c["s"])
    if not 0 <= idx < len(doc["busquedas"]):
        raise HTTPException(404)
    nueva = copy.deepcopy(doc["busquedas"][idx])
    nombres, n = {b["nombre"] for b in doc["busquedas"]}, 2
    while f"{nueva['nombre']}-{n}" in nombres:
        n += 1
    nueva["nombre"] = f"{nueva['nombre']}-{n}"
    doc["busquedas"].append(nueva)
    try:
        busqueda.guardar(c["s"], doc, c["user"].username)
    except ValueError as e:
        c["s"].rollback()
        return _responder(request, c, idx, berror=f"No se pudo copiar: {e}")
    return _responder(request, c, len(doc["busquedas"]) - 1, bok="Búsqueda copiada: ajustala y guardá.")


@router.post("/busqueda/{idx}/borrar", response_class=HTMLResponse)
def borrar(request: Request, idx: int, c=Depends(ctx)):
    _verificar(c)
    doc = busqueda.obtener(c["s"])
    if not 0 <= idx < len(doc["busquedas"]):
        raise HTTPException(404)
    if len(doc["busquedas"]) == 1:
        return _responder(request, c, idx, berror="Tiene que quedar al menos una búsqueda.")
    nombre = doc["busquedas"].pop(idx)["nombre"]
    fuera = busqueda.guardar(c["s"], doc, c["user"].username)
    return _responder(request, c, 0, bok=f"Búsqueda «{nombre}» borrada. {fuera} avisos fuera de los filtros.")
