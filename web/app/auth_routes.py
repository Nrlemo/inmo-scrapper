"""Instalación inicial, login/logout, cuenta propia y administración de usuarios (modo AUTH_MODE=basic)."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, config, security
from .auth import User
from .core import ctx, get_session, render, templates
from .models_web import Cuenta

router = APIRouter()
log = logging.getLogger("inmo.auth")
ip_login = security.Limitador(10, 600)      # 10 intentos de login cada 10 min por IP
ip_setup = security.Limitador(10, 600)
por_usuario_clave = security.Limitador(5, 600)   # cambios de clave fallidos/intentos por usuario
_GENERICO = "Usuario o contraseña incorrectos (o cuenta bloqueada temporalmente)."


def _solo_basic():
    if config.AUTH_MODE != "basic":
        raise HTTPException(404, "No disponible en este modo de autenticación")


def _ip(request: Request) -> str:
    return auth.client_ip(request)


def _pagina(request: Request, plantilla: str, status: int = 200, **kw):
    """Página sin sesión (login/setup) con su cookie CSRF de doble envío."""
    pre = request.cookies.get(auth.cookie_pre(request)) or security.nuevo_token(24)
    resp = templates.TemplateResponse(request, plantilla, {"csrf": pre, **kw}, status_code=status)
    auth.poner_cookie(resp, request, auth.cookie_pre(request), pre, max_age=3600, strict=True)
    return resp


def _csrf_pre_ok(request: Request, enviado: str) -> bool:
    cookie = request.cookies.get(auth.cookie_pre(request), "")
    return bool(cookie) and security.iguales(cookie, enviado)


def _entrar(request: Request, s: Session, cuenta: Cuenta, destino: str) -> Response:
    token = auth.crear_sesion(s, cuenta, request)
    resp = RedirectResponse("/cuenta" if cuenta.debe_cambiar else destino, status_code=303)
    auth.poner_cookie(resp, request, auth.cookie_sesion(request), token, max_age=int(config.SESSION_MAX_DAYS * 86400))
    auth.borrar_cookie(resp, request, auth.cookie_pre(request))
    return resp


# ---------------- instalación inicial ----------------
@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, s: Session = Depends(get_session)):
    if config.AUTH_MODE != "basic" or auth.hay_admin(s):
        return RedirectResponse("/", status_code=303)
    return _pagina(request, "setup.html", error=None, usuario="")


@router.post("/setup", response_class=HTMLResponse)
def setup_post(request: Request, usuario: str = Form(""), clave: str = Form(""), clave2: str = Form(""),
               codigo: str = Form(""), csrf: str = Form(""), s: Session = Depends(get_session)):
    if config.AUTH_MODE != "basic":
        raise HTTPException(404)
    usuario = security.normalizar_usuario(usuario)

    def fallo(msg: str, status: int = 400):
        return _pagina(request, "setup.html", status, error=msg, usuario=usuario)
    if not _csrf_pre_ok(request, csrf):
        return fallo("El formulario venció. Intentá de nuevo.")
    if not ip_setup.permitir(_ip(request)):
        return fallo("Demasiados intentos. Esperá unos minutos.", 429)
    with auth._lock_setup:                       # evita crear dos administradores a la vez
        if auth.hay_admin(s):
            raise HTTPException(404)
        if not security.iguales(codigo.strip().upper(), auth.codigo_instalacion().upper()):
            log.warning("setup: código de instalación incorrecto desde %s", _ip(request))
            return fallo("Código de instalación incorrecto.")
        err = security.validar_usuario(usuario) or security.validar_clave(clave, usuario)
        if not err and clave != clave2:
            err = "Las contraseñas no coinciden."
        if err:
            return fallo(err)
        cuenta = Cuenta(username=usuario, password_hash=security.hash_clave(clave), rol="admin", activo=True,
                        debe_cambiar=False, creado=datetime.now())
        s.add(cuenta)
        s.commit()
    log.info("setup: administrador %r creado desde %s", usuario, _ip(request))
    return _entrar(request, s, cuenta, "/")


# ---------------- login / logout ----------------
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", s: Session = Depends(get_session)):
    if config.AUTH_MODE != "basic":
        return RedirectResponse("/", status_code=303)
    if not auth.hay_admin(s):
        return RedirectResponse("/setup", status_code=303)
    if auth._usuario_por_cookie(request):
        return RedirectResponse(security.next_seguro(next), status_code=303)
    return _pagina(request, "login.html", error=None, usuario="", next=security.next_seguro(next))


@router.post("/login", response_class=HTMLResponse)
def login_post(request: Request, usuario: str = Form(""), clave: str = Form(""), next: str = Form("/"),
               csrf: str = Form(""), s: Session = Depends(get_session)):
    _solo_basic()
    usuario = security.normalizar_usuario(usuario)
    destino = security.next_seguro(next)

    def fallo(msg: str = _GENERICO, status: int = 401):
        return _pagina(request, "login.html", status, error=msg, usuario=usuario[:32], next=destino)
    if not _csrf_pre_ok(request, csrf):
        return fallo("El formulario venció. Intentá de nuevo.", 400)
    if not ip_login.permitir(_ip(request)):
        log.warning("login: límite por IP superado (%s)", _ip(request))
        resp = fallo("Demasiados intentos. Esperá unos minutos.", 429)
        resp.headers["Retry-After"] = "600"
        return resp
    cuenta, motivo = auth.verificar_credenciales(s, usuario, clave)
    if cuenta is None:
        log.warning("login fallido: usuario=%r ip=%s motivo=%s", usuario[:32], _ip(request), motivo)
        return fallo()
    log.info("login ok: usuario=%r ip=%s", cuenta.username, _ip(request))
    return _entrar(request, s, cuenta, destino)     # sesión nueva en cada login (evita fijación de sesión)


# ---------------- login para la app Android (JSON, sin CSRF de doble envío) ----------------
# La app nativa no tiene cookies ambientes que un sitio malicioso pueda aprovechar (a diferencia de un
# navegador): el usuario tipea user/pass en una pantalla propia de la app, así que el CSRF del formulario
# HTML no aplica acá. Sí comparte el mismo límite por IP y el mismo bloqueo por intentos que /login.
class LoginBody(BaseModel):
    usuario: str
    clave: str


@router.post("/api/login")
def api_login(request: Request, body: LoginBody, s: Session = Depends(get_session)):
    _solo_basic()
    usuario = security.normalizar_usuario(body.usuario)
    if not ip_login.permitir(_ip(request)):
        log.warning("api login: límite por IP superado (%s)", _ip(request))
        raise HTTPException(429, "Demasiados intentos. Esperá unos minutos.", headers={"Retry-After": "600"})
    cuenta, motivo = auth.verificar_credenciales(s, usuario, body.clave)
    if cuenta is None:
        log.warning("api login fallido: usuario=%r ip=%s motivo=%s", usuario[:32], _ip(request), motivo)
        raise HTTPException(401, _GENERICO)
    token = auth.crear_sesion(s, cuenta, request)   # mismo mecanismo de sesión que el login web
    log.info("api login ok: usuario=%r ip=%s", cuenta.username, _ip(request))
    return {
        "usuario": cuenta.username,
        "rol": cuenta.rol,
        "debe_cambiar": cuenta.debe_cambiar,
        "cookie_name": auth.cookie_sesion(request),
        "cookie_value": token,
        "max_age_seconds": int(config.SESSION_MAX_DAYS * 86400),
        "secure": auth.es_https(request),
    }


@router.post("/logout")
def logout(request: Request, c=Depends(ctx)):
    _solo_basic()
    u: User = c["user"]
    if u.sesion:
        from .models_web import Sesion
        row = c["s"].get(Sesion, u.sesion)
        if row:
            c["s"].delete(row)
            c["s"].commit()
    resp = Response(status_code=204, headers={"HX-Redirect": "/login"})
    auth.borrar_cookie(resp, request, auth.cookie_sesion(request))
    return resp


# ---------------- cuenta propia ----------------
def _cuenta_de(c) -> Cuenta:
    return c["s"].get(Cuenta, c["user"].cuenta_id)


@router.get("/cuenta", response_class=HTMLResponse)
def cuenta(request: Request, c=Depends(ctx)):
    _solo_basic()
    return render(request, "cuenta.html", c, cta=_cuenta_de(c), msg=None, error=None)


@router.post("/cuenta/clave", response_class=HTMLResponse)
def cambiar_clave(request: Request, actual: str = Form(""), nueva: str = Form(""), nueva2: str = Form(""),
                  c=Depends(ctx)):
    _solo_basic()
    cta, u = _cuenta_de(c), c["user"]
    error = msg = None
    if not por_usuario_clave.permitir(cta.username):
        error = "Demasiados intentos. Esperá unos minutos."
    elif not security.verificar_clave(cta.password_hash, actual):
        error = "La contraseña actual no es correcta."
    elif nueva != nueva2:
        error = "Las contraseñas nuevas no coinciden."
    elif nueva == actual:
        error = "La contraseña nueva debe ser distinta de la actual."
    else:
        error = security.validar_clave(nueva, cta.username)
    if not error:
        cta.password_hash, cta.debe_cambiar = security.hash_clave(nueva), False
        c["s"].commit()
        auth.cerrar_sesiones(c["s"], cta.id, excepto=u.sesion)    # el resto de las sesiones se cierra
        log.info("clave cambiada: usuario=%r", cta.username)
        msg = "Contraseña actualizada. Se cerraron tus otras sesiones."
    resp = templates.TemplateResponse(request, "partials/cuenta_clave.html", {"error": error, "msg": msg,
                                                                              "cta": cta}, status_code=400 if error else 200)
    if msg and u.debe_cambiar:
        resp.headers["HX-Redirect"] = "/"
    return resp


@router.post("/cuenta/cerrar-otras", response_class=HTMLResponse)
def cerrar_otras(request: Request, c=Depends(ctx)):
    _solo_basic()
    auth.cerrar_sesiones(c["s"], c["user"].cuenta_id, excepto=c["user"].sesion)
    return HTMLResponse('<span class="down">Se cerraron las demás sesiones.</span>')


# ---------------- administración de usuarios (sólo admin) ----------------
def admin_ctx(c=Depends(ctx)):
    _solo_basic()
    if not c["user"].es_admin:
        raise HTTPException(403, "Sólo para administradores")
    return c


def _admins_activos(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(Cuenta).where(Cuenta.rol == "admin", Cuenta.activo.is_(True)))


def _lista(request: Request, c, **kw):
    cuentas = c["s"].scalars(select(Cuenta).order_by(Cuenta.username)).all()
    return templates.TemplateResponse(request, "partials/usuarios_lista.html",
                                      {"cuentas": cuentas, "yo": c["user"], "ahora": datetime.now(), **kw})


@router.get("/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, c=Depends(admin_ctx)):
    cuentas = c["s"].scalars(select(Cuenta).order_by(Cuenta.username)).all()
    return render(request, "usuarios.html", c, cuentas=cuentas, yo=c["user"], ahora=datetime.now(), error=None, aviso=None)


@router.post("/usuarios/crear", response_class=HTMLResponse)
def crear(request: Request, usuario: str = Form(""), clave: str = Form(""), rol: str = Form("usuario"),
          c=Depends(admin_ctx)):
    s = c["s"]
    usuario = security.normalizar_usuario(usuario)
    error = security.validar_usuario(usuario)
    if not error and rol not in ("admin", "usuario"):
        error = "Rol inválido."
    if not error and s.scalar(select(Cuenta.id).where(Cuenta.username == usuario)):
        error = "Ya existe un usuario con ese nombre."
    generada = None
    if not error:
        if not clave:
            clave = generada = security.generar_clave()
        else:
            error = security.validar_clave(clave, usuario)
    if error:
        return _lista(request, c, error=error, aviso=None)
    s.add(Cuenta(username=usuario, password_hash=security.hash_clave(clave), rol=rol, activo=True,
                 debe_cambiar=True, creado=datetime.now()))
    s.commit()
    log.info("usuario %r (%s) creado por %r", usuario, rol, c["user"].username)
    aviso = f"Usuario «{usuario}» creado. Deberá cambiar la contraseña al ingresar."
    return _lista(request, c, error=None, aviso=aviso, clave_temp=generada, clave_de=usuario)


def _objetivo(c, uid: int) -> Cuenta:
    cta = c["s"].get(Cuenta, uid)
    if cta is None:
        raise HTTPException(404)
    return cta


@router.post("/usuarios/{uid}/activo", response_class=HTMLResponse)
def activo(request: Request, uid: int, c=Depends(admin_ctx)):
    s, cta = c["s"], _objetivo(c, uid)
    error = None
    if cta.id == c["user"].cuenta_id:
        error = "No podés desactivar tu propia cuenta."
    elif cta.activo and cta.rol == "admin" and _admins_activos(s) <= 1:
        error = "Tiene que quedar al menos un administrador activo."
    else:
        cta.activo = not cta.activo
        s.commit()
        if not cta.activo:
            auth.cerrar_sesiones(s, cta.id)
        log.info("usuario %r %s por %r", cta.username, "activado" if cta.activo else "desactivado", c["user"].username)
    return _lista(request, c, error=error, aviso=None)


@router.post("/usuarios/{uid}/rol", response_class=HTMLResponse)
def rol(request: Request, uid: int, c=Depends(admin_ctx)):
    s, cta = c["s"], _objetivo(c, uid)
    error = None
    if cta.rol == "admin" and cta.activo and _admins_activos(s) <= 1:
        error = "Tiene que quedar al menos un administrador activo."
    else:
        cta.rol = "usuario" if cta.rol == "admin" else "admin"
        s.commit()
        auth.cerrar_sesiones(s, cta.id)     # el rol viaja con la cuenta; se obliga a re-ingresar
        log.info("rol de %r ahora %s (por %r)", cta.username, cta.rol, c["user"].username)
    return _lista(request, c, error=error, aviso=None)


@router.post("/usuarios/{uid}/clave", response_class=HTMLResponse)
def resetear(request: Request, uid: int, c=Depends(admin_ctx)):
    s, cta = c["s"], _objetivo(c, uid)
    nueva = security.generar_clave()
    cta.password_hash, cta.debe_cambiar, cta.fallos, cta.bloqueado_hasta = security.hash_clave(nueva), True, 0, None
    s.commit()
    auth.cerrar_sesiones(s, cta.id, excepto=c["user"].sesion if cta.id == c["user"].cuenta_id else None)
    log.info("clave de %r restablecida por %r", cta.username, c["user"].username)
    return _lista(request, c, error=None, aviso=f"Contraseña de «{cta.username}» restablecida.", clave_temp=nueva,
                  clave_de=cta.username)
