"""Todas (listado con filtros y búsquedas guardadas), Ranking, Precios (cambios), Comparar, Mapa y export a CSV."""
import csv
import io
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import text

from . import config, queries
from .core import ctx, render
from .models_web import BusquedaGuardada
from .queries import Filtros

router = APIRouter()


def _filtros(request: Request, desde) -> Filtros:
    g = request.query_params.get

    def num(k, cast=float):
        try:
            return cast(g(k)) if g(k) else None
        except ValueError:
            return None
    return Filtros(q=(g("q") or "").strip(), barrio=g("barrio") or "", portal=g("portal") or "",
                   inmo=num("inmo", int), pmin=num("pmin"), pmax=num("pmax"), mmin=num("mmin"), amb=num("amb", int),
                   cochera=g("cochera") == "1", bajo_barrio=g("bajo") == "1", etiqueta=(g("etiqueta") or "").strip(), estado=g("estado") or "", baja=g("baja") == "1",
                   nuevas=g("nuevas") == "1", inactivas=g("inactivas") == "1", fuera=g("fuera") == "1", orden=g("orden") or "nuevas",
                   pagina=num("pagina", int) or 1, desde=desde)


def _lista_ctx(request: Request, c):
    f = _filtros(request, c["desde"])
    rows, total = queries.listar(c["s"].connection(), f, config.PAGE_SIZE)
    qs = {k: v for k, v in request.query_params.items() if k != "pagina" and v}
    return {"f": f, "rows": rows, "total": total, "qs": urlencode(qs),
            "paginas": max(1, -(-total // config.PAGE_SIZE))}


@router.get("/lista", response_class=HTMLResponse)
def lista(request: Request, c=Depends(ctx)):
    d = _lista_ctx(request, c)
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "resultados":
        return render(request, "partials/resultados.html", c, **d)
    conn = c["s"].connection()
    guardadas = [dict(r) for r in conn.execute(text("SELECT id, nombre, querystring FROM web_busquedas ORDER BY nombre")).mappings()]
    return render(request, "lista.html", c, **d, barrios=queries.barrios(conn), portales=queries.portales(conn),
                  inmos=queries.inmobiliarias(conn), etiquetas=queries.etiquetas(conn), guardadas=guardadas)


@router.post("/busquedas", response_class=HTMLResponse)
def guardar_busqueda(nombre: str = Form(...), querystring: str = Form(""), c=Depends(ctx)):
    s = c["s"]
    s.add(BusquedaGuardada(nombre=nombre.strip()[:80] or "Búsqueda", querystring=querystring, usuario=c["user"].username))
    s.commit()
    return Response(headers={"HX-Redirect": "/lista?" + querystring})


@router.post("/busquedas/{bid}/borrar")
def borrar_busqueda(bid: int, c=Depends(ctx)):
    c["s"].execute(text("DELETE FROM web_busquedas WHERE id=:i"), {"i": bid})
    c["s"].commit()
    return Response(headers={"HX-Redirect": "/lista"})


@router.get("/ranking", response_class=HTMLResponse)
def ranking(request: Request, usuario: str = "", inactivas: int = 0, descartadas: int = 0, c=Depends(ctx)):
    rows, usuarios = queries.ranking(c["s"].connection(), usuario, bool(inactivas), bool(descartadas),
                                     solo_cuentas=config.AUTH_MODE == "basic")
    return render(request, "ranking.html", c, rows=rows, usuarios=usuarios, sel=usuario,
                  inactivas=inactivas, descartadas=descartadas)


@router.get("/cambios", response_class=HTMLResponse)
def cambios(request: Request, dias: int = 30, favoritas: int = 0, c=Depends(ctx)):
    rows = queries.cambios(c["s"].connection(), min(max(dias, 1), 365), bool(favoritas))
    return render(request, "cambios.html", c, rows=rows, dias=dias, favoritas=favoritas)


@router.get("/comparar", response_class=HTMLResponse)
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
    return render(request, "comparar.html", c, props=props, stats=stats, favoritas=usando_favoritas)


@router.get("/mapa", response_class=HTMLResponse)
def mapa(request: Request, c=Depends(ctx)):
    n = c["s"].connection().execute(text("SELECT COUNT(*) FROM publicaciones WHERE lat IS NOT NULL AND activa=1 "
                                         "AND COALESCE(fuera_filtro,0)=0")).scalar_one()
    return render(request, "mapa.html", c, con_coords=n)


@router.get("/api/mapa")
def api_mapa(request: Request, c=Depends(ctx)):
    f = _filtros(request, c["desde"])
    return JSONResponse(queries.mapa(c["s"].connection(), f))


@router.get("/export.csv")
def export(request: Request, c=Depends(ctx)):
    f = _filtros(request, c["desde"])
    rows, _ = queries.listar(c["s"].connection(), f, 100000)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "portal", "url", "barrio", "direccion", "precio", "moneda", "m2_cub", "ambientes", "usd_m2", "vs_barrio_pct",
                "favorito", "potencial", "descartada", "contactada", "puntaje_promedio", "votos", "notas"])
    for p in rows:
        def safe(v):  # evita inyección de fórmulas al abrir en planilla
            return "'" + v if isinstance(v, str) and v[:1] in "=+-@" else v
        w.writerow([p["id"], p["portal"], p["url"], safe(p["barrio"]), safe(p["direccion"]), p["precio"], p["moneda"],
                    p["m2_cubiertos"], p["ambientes"], round(p["usd_m2"] or 0) or "", p["vb_pct"] if p["vb_pct"] is not None else "", p["favorito"], p["potencial"],
                    p["descartada"], p["contactada"], round(p["puntaje"], 2) if p["puntaje"] else "", p["votos"],
                    safe(p["notas"])])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=propiedades.csv"})
