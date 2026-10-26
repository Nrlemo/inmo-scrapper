"""Ronda por navegador: API para la extensión (token propio, sin cookies ni CSRF) y gestión del token en Estado.

La extensión (repo aparte: https://github.com/Nrlemo/inmo-extension) abre las búsquedas de Zonaprop en el navegador
del usuario y manda cada página a /api/navegador/pagina; la lógica de la ronda está en `inmo.navegador`.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import navegador

from . import auth, busqueda, config, security
from .core import ctx, get_auth_session, render, sesion_de
from .models_web import TokenApi

router = APIRouter()
log = logging.getLogger("inmo.navegador")
MAX_HTML = 8 * 1024 * 1024          # una página de resultados de Zonaprop pesa ~1,5 MB


def usuario_token(request: Request, s: Session = Depends(get_auth_session)) -> str:
    """Identidad por `Authorization: Bearer <token>`. Actualiza el último uso."""
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else ""
    t = s.scalar(select(TokenApi).where(TokenApi.hash == security.hash_token(token))) if token else None
    if t is None:
        raise HTTPException(401, "Token inválido: generá uno en Estado → Ronda por navegador")
    t.ultimo_uso = datetime.now()
    s.commit()
    return t.usuario


# `base` (compra | alquiler) es aditivo: la extensión 1.1.0, que no la manda, trabaja con la base de compra. Los ids de
# ronda son de cada base, por eso viaja en todos los pedidos.
class Pagina(BaseModel):
    base: str = "compra"
    ronda: int
    url: str
    html: str = Field(max_length=MAX_HTML)


class PedidoRonda(BaseModel):
    base: str = "compra"
    forzar: bool = False          # «Correr ronda ahora»: ignora el intervalo mínimo (no el cooldown por bloqueo)


class ErrorCarga(BaseModel):
    base: str = "compra"
    ronda: int
    url: str
    motivo: str = Field(max_length=500)


@router.get("/api/navegador/ping")
def ping(usuario: str = Depends(usuario_token)):
    return {"ok": True, "usuario": usuario}


def _otras_bases(request: Request, base: str, cfg: dict) -> tuple[bool, dict]:
    """(¿hay una ronda corriendo en otra base?, bloqueos de portales registrados en las otras bases). Compra y alquiler
    leen los mismos portales desde el mismo navegador: no se corren a la vez y el bloqueo de uno frena al otro."""
    activa, bloqueos = False, {}
    for otra in config.BASES:
        if otra == base:
            continue
        with sesion_de(request, otra) as s:
            activa = activa or navegador.en_curso(s)
            for portal, hasta in navegador.bloqueos(s, cfg).items():
                bloqueos[portal] = max(hasta, bloqueos.get(portal, hasta))
    return activa, bloqueos


@router.post("/api/navegador/ronda")
def iniciar(request: Request, body: PedidoRonda | None = None, usuario: str = Depends(usuario_token)):
    base = auth.base_valida(body.base if body else None)
    with sesion_de(request, base) as s:
        cfg = busqueda.config_efectiva(s)
        activa, bloqueos = _otras_bases(request, base, cfg)
        if activa:
            return {"base": base, "omitir": "hay una ronda en curso en la otra base (compra/alquiler): se espera a que termine"}
        r = navegador.iniciar(s, cfg, usuario, forzar=bool(body and body.forzar), bloqueos_externos=bloqueos)
    log.info("ronda por navegador (%s) pedida por %s: %s", base, usuario, r.get("omitir") or f"ronda {r['ronda']}")
    return {**r, "base": base}       # una versión anterior del servidor no la repite: la extensión se da cuenta


@router.post("/api/navegador/pagina")
def pagina(request: Request, body: Pagina, usuario: str = Depends(usuario_token)):
    with sesion_de(request, body.base) as s:
        return navegador.pagina(s, busqueda.config_efectiva(s), body.ronda, body.url, body.html)


@router.post("/api/navegador/error")
def error(request: Request, body: ErrorCarga, usuario: str = Depends(usuario_token)):
    log.warning("ronda %d (%s): %s no cargó (%s)", body.ronda, body.base, body.url, body.motivo)
    with sesion_de(request, body.base) as s:
        return navegador.error(s, busqueda.config_efectiva(s), body.ronda, body.url, body.motivo)


class Ronda(BaseModel):
    base: str = "compra"
    ronda: int


@router.post("/api/navegador/cancelar")
def cancelar(request: Request, body: Ronda, usuario: str = Depends(usuario_token)):
    with sesion_de(request, body.base) as s:
        return navegador.cancelar(s, body.ronda)


# ---------------- token (pantalla Estado) ----------------
def token_de(s: Session, usuario: str) -> TokenApi | None:
    return s.scalar(select(TokenApi).where(TokenApi.usuario == usuario))


@router.post("/navegador/token", response_class=HTMLResponse)
def generar_token(request: Request, c=Depends(ctx)):
    """Genera (o reemplaza) el token del usuario y lo muestra una sola vez."""
    s, usuario = c["sa"], c["user"].username
    token = security.nuevo_token()
    t = token_de(s, usuario) or TokenApi(usuario=usuario)
    t.hash, t.creado, t.ultimo_uso = security.hash_token(token), datetime.now(), None
    s.add(t)
    s.commit()
    return render(request, "partials/navegador.html", c, tok=t, nuevo=token)


@router.post("/navegador/token/borrar", response_class=HTMLResponse)
def borrar_token(request: Request, c=Depends(ctx)):
    s = c["sa"]
    t = token_de(s, c["user"].username)
    if t:
        s.delete(t)
        s.commit()
    return render(request, "partials/navegador.html", c, tok=None)
