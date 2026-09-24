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

from . import busqueda, security
from .core import ctx, get_session, render
from .models_web import TokenApi

router = APIRouter()
log = logging.getLogger("inmo.navegador")
MAX_HTML = 8 * 1024 * 1024          # una página de resultados de Zonaprop pesa ~1,5 MB


def usuario_token(request: Request, s: Session = Depends(get_session)) -> str:
    """Identidad por `Authorization: Bearer <token>`. Actualiza el último uso."""
    h = request.headers.get("authorization", "")
    token = h[7:].strip() if h.lower().startswith("bearer ") else ""
    t = s.scalar(select(TokenApi).where(TokenApi.hash == security.hash_token(token))) if token else None
    if t is None:
        raise HTTPException(401, "Token inválido: generá uno en Estado → Ronda por navegador")
    t.ultimo_uso = datetime.now()
    s.commit()
    return t.usuario


class Pagina(BaseModel):
    ronda: int
    url: str
    html: str = Field(max_length=MAX_HTML)


class PedidoRonda(BaseModel):
    forzar: bool = False          # «Correr ronda ahora»: ignora el intervalo mínimo (no el cooldown por bloqueo)


class ErrorCarga(BaseModel):
    ronda: int
    url: str
    motivo: str = Field(max_length=500)


@router.get("/api/navegador/ping")
def ping(usuario: str = Depends(usuario_token)):
    return {"ok": True, "usuario": usuario}


@router.post("/api/navegador/ronda")
def iniciar(body: PedidoRonda | None = None, usuario: str = Depends(usuario_token), s: Session = Depends(get_session)):
    r = navegador.iniciar(s, busqueda.config_efectiva(s), usuario, forzar=bool(body and body.forzar))
    log.info("ronda por navegador pedida por %s: %s", usuario, r.get("omitir") or f"ronda {r['ronda']}")
    return r


@router.post("/api/navegador/pagina")
def pagina(body: Pagina, usuario: str = Depends(usuario_token), s: Session = Depends(get_session)):
    return navegador.pagina(s, busqueda.config_efectiva(s), body.ronda, body.url, body.html)


@router.post("/api/navegador/error")
def error(body: ErrorCarga, usuario: str = Depends(usuario_token), s: Session = Depends(get_session)):
    log.warning("ronda %d: %s no cargó (%s)", body.ronda, body.url, body.motivo)
    return navegador.error(s, busqueda.config_efectiva(s), body.ronda, body.url, body.motivo)


class Ronda(BaseModel):
    ronda: int


@router.post("/api/navegador/cancelar")
def cancelar(body: Ronda, usuario: str = Depends(usuario_token), s: Session = Depends(get_session)):
    return navegador.cancelar(s, body.ronda)


# ---------------- token (pantalla Estado) ----------------
def token_de(s: Session, usuario: str) -> TokenApi | None:
    return s.scalar(select(TokenApi).where(TokenApi.usuario == usuario))


@router.post("/navegador/token", response_class=HTMLResponse)
def generar_token(request: Request, c=Depends(ctx)):
    """Genera (o reemplaza) el token del usuario y lo muestra una sola vez."""
    s, usuario = c["s"], c["user"].username
    token = security.nuevo_token()
    t = token_de(s, usuario) or TokenApi(usuario=usuario)
    t.hash, t.creado, t.ultimo_uso = security.hash_token(token), datetime.now(), None
    s.add(t)
    s.commit()
    return render(request, "partials/navegador.html", c, tok=t, nuevo=token)


@router.post("/navegador/token/borrar", response_class=HTMLResponse)
def borrar_token(request: Request, c=Depends(ctx)):
    s = c["s"]
    t = token_de(s, c["user"].username)
    if t:
        s.delete(t)
        s.commit()
    return render(request, "partials/navegador.html", c, tok=None)
