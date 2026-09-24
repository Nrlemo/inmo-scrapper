"""Revisión: la cola (/), el detalle de un aviso (/p/{pid}) y sus acciones (estado, notas, puntaje, etiquetas)."""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import queries
from .core import ctx, render
from .models_web import Evento, Puntaje, Revision

router = APIRouter()

ESTADO_CAT = (("descartada", "descartado"), ("contactada", "contactado"), ("favorito", "interesante"), ("potencial", "interesante"))
ACCIONES = {"favorito", "potencial", "descartar", "contactada", "restaurar"}


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


def _detalle_extra(s, p, vista, user: str):
    if vista != "detalle":
        return {}
    conn = s.connection()
    return {"hist": queries.historial(conn, p["id"]), "hermanas": queries.hermanas(conn, p),
            "eventos": queries.eventos(conn, p["id"]), **_puntajes_ctx(conn, p["id"], user)}


def _puntajes_ctx(conn, pid: int, user: str) -> dict:
    return {"mio": queries.mi_puntaje(conn, pid, user), "puntajes": queries.puntajes(conn, pid)}


@router.get("/", response_class=HTMLResponse)
def revision(request: Request, despues: int | None = None, c=Depends(ctx)):
    conn = c["s"].connection()
    p, total = queries.siguiente_pendiente(conn, despues)
    tpl = "partials/card.html" if request.headers.get("HX-Request") else "revision.html"
    return render(request, tpl, c, p=p, total=total, hoy=queries.revisadas_hoy(conn, c["user"].username))


@router.post("/p/{pid}/accion", response_class=HTMLResponse)
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


@router.post("/p/{pid}/notas")
def notas(pid: int, notas: str = Form(""), c=Depends(ctx)):
    s, user = c["s"], c["user"].username
    r = _rev(s, pid, user)
    r.notas = notas.strip()[:4000] or None
    _log(s, pid, user, "notas")
    s.commit()
    return Response(status_code=204)


@router.post("/p/{pid}/puntaje", response_class=HTMLResponse)
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


@router.post("/p/{pid}/etiquetas")
def etiquetas(pid: int, etiquetas: str = Form(""), c=Depends(ctx)):
    s, user = c["s"], c["user"].username
    _rev(s, pid, user)
    tags = sorted({t.strip().lower()[:30] for t in etiquetas.split(",") if t.strip()})[:12]
    s.execute(text("UPDATE categorizacion SET etiquetas=:t, fecha_modificacion=:f WHERE publicacion_id=:i"),
              {"t": json.dumps(tags), "f": datetime.now(), "i": pid})
    _log(s, pid, user, "etiquetas", ",".join(tags))
    s.commit()
    return Response(status_code=204)


@router.get("/p/{pid}", response_class=HTMLResponse)
def detalle(request: Request, pid: int, panel: int = 0, c=Depends(ctx)):
    p = queries.uno(c["s"].connection(), pid)
    if not p:
        raise HTTPException(404)
    extra = _detalle_extra(c["s"], p, "detalle", c["user"].username)
    return render(request, "partials/detalle.html" if panel else "detalle.html", c, p=p, vista="detalle", **extra)
