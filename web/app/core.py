"""Piezas compartidas por las rutas: plantillas, dependencias (sesión, ctx) y cabeceras de seguridad."""
import re
import time
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.orm import Session

from . import config, queries
from .auth import PasswordChangeRequired, User, current_user, es_https
from .models_web import Usuario
from .security import iguales

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


# Siglas que se dejan en mayúsculas al prolijar direcciones
_SIGLAS = {"PB", "PH", "CABA", "UF", "SUM", "II", "III", "IV", "VI", "SA", "SRL"}


def _dir(s: str | None) -> str:
    """Direcciones más prolijas: palabras TODO EN MAYÚSCULAS pasan a «Capitalizada» («AV. CORDOBA» -> «Av. Cordoba»),
    salvo siglas conocidas. Lo que ya viene en minúsculas o mixto no se toca."""
    if not s:
        return s or ""
    out = []
    for w in re.split(r"(\s+)", s):
        letras = re.sub(r"[^A-Za-zÁÉÍÓÚÑÜáéíóúñü]", "", w)
        if len(letras) >= 2 and letras.isupper() and letras not in _SIGLAS:
            w = w[:1] + w[1:].lower()
        elif w in ("Y", "E", "O") and out:      # «PARAGUAY Y ESMERALDA» -> «Paraguay y Esmeralda»
            w = w.lower()
        out.append(w)
    return "".join(out)


_portales_cache: tuple[float, bool] = (0.0, False)


def varios_portales() -> bool:
    """¿Hay más de un portal configurado? Si hay uno solo, no tiene sentido mostrar el nombre en cada aviso."""
    global _portales_cache
    ahora = time.monotonic()
    if ahora - _portales_cache[0] > 60:
        from .scrapper_ctl import portales_y_zonas
        _portales_cache = (ahora, len(portales_y_zonas()) > 1)
    return _portales_cache[1]


templates.env.filters.update(money=_money, fecha=_fecha, ago=_ago, dir=_dir)
templates.env.globals.update(spark=spark, varios_portales=varios_portales)


# ---------- dependencias ----------
def get_session(request: Request):
    with Session(request.app.state.engine) as s:
        yield s


SEGUROS = ("GET", "HEAD", "OPTIONS")


def ctx(request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Usuario + protección CSRF + control de 'última visita' (una sesión = >30 min sin actividad)."""
    if request.method not in SEGUROS:
        if request.headers.get("HX-Request") != "true":
            raise HTTPException(403, "Solicitud no permitida")
        if user.csrf is not None and not iguales(request.headers.get("X-CSRF-Token", ""), user.csrf):
            raise HTTPException(403, "Token CSRF inválido: recargá la página")
    path = request.url.path
    if user.debe_cambiar and not (path.startswith("/cuenta") or path == "/logout"):
        raise PasswordChangeRequired()
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
    data = {"user": c["user"], "auth_mode": config.AUTH_MODE, "cont": queries.contadores(conn, c["desde"]), **kw}
    return templates.TemplateResponse(request, name, data)


async def headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src * data:; style-src 'self' 'unsafe-inline'; "
                                            "script-src 'self' 'unsafe-inline'; frame-src https:; frame-ancestors 'self'; "
                                            "form-action 'self'; base-uri 'self'")
    if config.AUTH_MODE == "basic":
        if es_https(request):
            r.headers["Strict-Transport-Security"] = "max-age=31536000"
        if r.headers.get("content-type", "").startswith("text/html"):
            r.headers["Cache-Control"] = "no-store"   # páginas privadas: no dejarlas en cachés del navegador/proxy
    return r
