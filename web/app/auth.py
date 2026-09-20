"""Identidad de usuario según AUTH_MODE: basic (sesiones propias), authentik (cabeceras del proxy) o none."""
import hmac
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import config, security
from .models_web import Cuenta, Sesion

log = logging.getLogger("inmo.auth")


class LoginRequired(Exception):
    """Hace falta iniciar sesión (modo basic)."""


class SetupRequired(Exception):
    """Todavía no existe ningún administrador: hay que hacer la instalación inicial."""


class PasswordChangeRequired(Exception):
    """La cuenta tiene una clave temporal: debe cambiarla antes de seguir."""


@dataclass
class User:
    username: str
    email: str | None = None
    rol: str = "usuario"
    csrf: str | None = None         # token CSRF de la sesión (sólo modo basic)
    debe_cambiar: bool = False
    cuenta_id: int | None = None
    sesion: str | None = None       # hash de la sesión actual

    @property
    def es_admin(self) -> bool:
        return config.AUTH_MODE == "basic" and self.rol == "admin"


# ---------- cliente / cookies ----------
def _reenviado(request: Request, cabecera: str) -> str | None:
    """Valor de una cabecera X-Forwarded-* agregado por nuestro proxy (el n-ésimo desde la derecha)."""
    hops = config.TRUSTED_PROXY_HOPS
    vals = [v.strip() for v in request.headers.get(cabecera, "").split(",") if v.strip()]
    return vals[-hops] if hops > 0 and len(vals) >= hops else None


def client_ip(request: Request) -> str:
    return _reenviado(request, "x-forwarded-for") or (request.client.host if request.client else "?")


def es_https(request: Request) -> bool:
    if config.COOKIE_SECURE in ("true", "1", "yes"):
        return True
    if config.COOKIE_SECURE in ("false", "0", "no"):
        return False
    return (_reenviado(request, "x-forwarded-proto") or request.url.scheme) == "https"


def _nombre(base: str, request: Request) -> str:
    return f"__Host-{base}" if es_https(request) else base   # __Host-: Secure, sin Domain, Path=/


def cookie_sesion(request: Request) -> str:
    return _nombre("inmo_session", request)


def cookie_pre(request: Request) -> str:
    return _nombre("inmo_csrf", request)


def poner_cookie(response, request: Request, nombre: str, valor: str, max_age: int | None = None, strict: bool = False):
    response.set_cookie(nombre, valor, max_age=max_age, path="/", httponly=True, secure=es_https(request),
                        samesite="strict" if strict else "lax")


def borrar_cookie(response, request: Request, nombre: str):
    response.delete_cookie(nombre, path="/", httponly=True, secure=es_https(request), samesite="lax")


# ---------- instalación inicial ----------
_codigo: str | None = None
_lock_setup = threading.Lock()


def codigo_instalacion() -> str:
    """Código que hay que ingresar para crear el primer administrador (SETUP_TOKEN o uno aleatorio, en el log)."""
    global _codigo
    if config.SETUP_TOKEN:
        return config.SETUP_TOKEN
    if _codigo is None:
        _codigo = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    return _codigo


def hay_admin(s: Session) -> bool:
    return s.scalar(select(func.count()).select_from(Cuenta).where(Cuenta.rol == "admin", Cuenta.activo.is_(True))) > 0


def avisar_instalacion(engine) -> None:
    """Al arrancar en modo basic sin administrador, informa en el log cómo completar la instalación."""
    with Session(engine) as s:
        if hay_admin(s):
            return
    origen = "SETUP_TOKEN" if config.SETUP_TOKEN else "generado al azar"
    log.warning("INSTALACIÓN PENDIENTE: abrí /setup para crear el administrador. Código de instalación (%s): %s",
                origen, codigo_instalacion())


# ---------- sesiones ----------
def crear_sesion(s: Session, cuenta: Cuenta, request: Request) -> str:
    now = datetime.now()
    s.execute(delete(Sesion).where(Sesion.expira < now))
    token = security.nuevo_token()
    s.add(Sesion(id_hash=security.hash_token(token), cuenta_id=cuenta.id, csrf=security.nuevo_token(24), creada=now,
                 actividad=now, expira=now + timedelta(days=config.SESSION_MAX_DAYS),
                 ip=client_ip(request)[:45],
                 agente=(request.headers.get("user-agent") or "")[:200]))
    cuenta.ultimo_login = now
    s.commit()
    return token


def cerrar_sesiones(s: Session, cuenta_id: int, excepto: str | None = None) -> None:
    q = delete(Sesion).where(Sesion.cuenta_id == cuenta_id)
    if excepto:
        q = q.where(Sesion.id_hash != excepto)
    s.execute(q)
    s.commit()


def _usuario_por_cookie(request: Request) -> User | None:
    token = request.cookies.get(cookie_sesion(request))
    if not token:
        return None
    engine = request.app.state.engine
    now = datetime.now()
    with Session(engine) as s:
        row = s.get(Sesion, security.hash_token(token))
        if row is None:
            return None
        if now >= row.expira or now - row.actividad > timedelta(hours=config.SESSION_IDLE_HOURS):
            s.delete(row)
            s.commit()
            return None
        cuenta = s.get(Cuenta, row.cuenta_id)
        if cuenta is None or not cuenta.activo:
            s.delete(row)
            s.commit()
            return None
        if now - row.actividad > timedelta(seconds=60):
            row.actividad = now
            s.commit()
        return User(cuenta.username, None, cuenta.rol, row.csrf, cuenta.debe_cambiar, cuenta.id, row.id_hash)


# ---------- dependencia principal ----------
def current_user(request: Request) -> User:
    mode = config.AUTH_MODE
    if mode == "none":  # sin autenticación: no se exige identidad ni secreto del proxy
        return User(request.headers.get(config.USER_HEADER) or config.DEFAULT_USER,
                    request.headers.get(config.EMAIL_HEADER))
    if mode == "authentik":
        if config.PROXY_SECRET and not hmac.compare_digest(
                request.headers.get("X-Proxy-Secret", ""), config.PROXY_SECRET):
            raise HTTPException(401, "Acceso sólo a través del proxy")
        name = request.headers.get(config.USER_HEADER)
        if not name:
            raise HTTPException(401, "Sin identidad de authentik")
        return User(name, request.headers.get(config.EMAIL_HEADER))
    # basic
    u = _usuario_por_cookie(request)
    if u:
        return u
    with Session(request.app.state.engine) as s:
        if not hay_admin(s):
            raise SetupRequired()
    raise LoginRequired()
