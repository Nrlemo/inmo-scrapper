"""Respuestas de error: redirecciones de autenticación (login, instalación, cambio de clave) y páginas de error.
Con HTMX se responde con HX-Redirect; bajo /api/, JSON (la app Android y la extensión no esperan HTML)."""
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import LoginRequired, PasswordChangeRequired, SetupRequired
from .core import templates

TITULOS = {401: "Acceso no autorizado", 403: "Acceso denegado", 404: "No encontrado", 405: "Método no permitido"}


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


async def _login_required(request: Request, exc: LoginRequired):
    if _hx(request):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "no autenticado"}, status_code=401)
    destino = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    nxt = f"?next={quote(destino, safe='')}" if request.method == "GET" and request.url.path != "/" else ""
    return RedirectResponse("/login" + nxt, status_code=303)


async def _setup_required(request: Request, exc: SetupRequired):
    if _hx(request):
        return Response(status_code=401, headers={"HX-Redirect": "/setup"})
    return RedirectResponse("/setup", status_code=303)


async def _must_change(request: Request, exc: PasswordChangeRequired):
    if _hx(request):
        return Response(status_code=403, headers={"HX-Redirect": "/cuenta"})
    return RedirectResponse("/cuenta", status_code=303)


async def http_err(request: Request, exc: StarletteHTTPException):
    """Página de error simple. Los errores de autenticación/permisos (401/403) llevan el GIF."""
    code = exc.status_code
    if request.url.path.startswith("/api/"):   # la app Android (y /api/mapa) esperan JSON, no la página de error
        return JSONResponse({"detail": str(exc.detail)}, status_code=code, headers=getattr(exc, "headers", None))
    return templates.TemplateResponse(
        request, "error.html",
        {"titulo": TITULOS.get(code, f"Error {code}"), "detalle": str(exc.detail), "gif": code in (401, 403)},
        status_code=code, headers=getattr(exc, "headers", None))


def registrar(app: FastAPI) -> None:
    app.add_exception_handler(LoginRequired, _login_required)
    app.add_exception_handler(SetupRequired, _setup_required)
    app.add_exception_handler(PasswordChangeRequired, _must_change)
    app.add_exception_handler(StarletteHTTPException, http_err)
