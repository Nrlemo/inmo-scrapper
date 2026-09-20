import re
from datetime import datetime, timedelta

import pytest

PW = "Correct-horse-battery-9"
CODE = "TEST-CODE-1234"


def _csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _meta(html):
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def setup_admin(c, user="admin", pw=PW, code=CODE):
    tok = _csrf(c.get("/setup").text)
    return c.post("/setup", data={"usuario": user, "clave": pw, "clave2": pw, "codigo": code, "csrf": tok})


def login(c, user, pw, nxt="/"):
    tok = _csrf(c.get(f"/login?next={nxt}").text)
    return c.post("/login", data={"usuario": user, "clave": pw, "next": nxt, "csrf": tok})


def hx(c):
    """Cabeceras de un pedido HTMX legítimo (con el token CSRF de la sesión)."""
    return {"HX-Request": "true", "X-CSRF-Token": _meta(c.get("/cuenta").text)}


def new_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def admin(basic):
    assert setup_admin(basic).status_code == 303
    return basic


# ---------------- instalación inicial ----------------
def test_everything_redirects_to_setup_until_admin_exists(basic):
    for url in ["/", "/lista", "/login", "/estado", "/usuarios"]:
        r = basic.get(url)
        assert r.status_code == 303 and r.headers["location"] == "/setup", url
    assert basic.get("/healthz").status_code == 200
    assert basic.get("/static/app.css").status_code == 200
    assert "Crear administrador" in basic.get("/setup").text


def test_setup_validations_and_success(basic):
    tok = _csrf(basic.get("/setup").text)
    base = {"usuario": "admin", "clave": PW, "clave2": PW, "codigo": CODE, "csrf": tok}
    assert basic.post("/setup", data={**base, "csrf": "otro"}).status_code == 400            # CSRF
    assert "código de instalación incorrecto" in basic.post("/setup", data={**base, "codigo": "MAL"}).text.lower()
    assert "al menos 12" in basic.post("/setup", data={**base, "clave": "corta", "clave2": "corta"}).text
    assert "no coinciden" in basic.post("/setup", data={**base, "clave2": PW + "x"}).text
    assert "no puede contener el nombre" in basic.post("/setup", data={**base, "usuario": "carlos", "clave": "xx-carlos-xx-99", "clave2": "xx-carlos-xx-99"}).text
    assert "Usuario inválido" in basic.post("/setup", data={**base, "usuario": "a b"}).text
    r = basic.post("/setup", data=base)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers.get_list("set-cookie")[0].lower()
    assert "inmo_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert basic.get("/").status_code == 200                                                # ya está adentro
    assert basic.get("/setup").status_code == 303                                           # el asistente se cierra
    assert new_client().post("/setup", data=base).status_code in (400, 404)                 # y no se puede repetir


def test_setup_code_is_random_when_not_configured(basic, monkeypatch):
    import app.auth as auth
    monkeypatch.setattr("app.config.SETUP_TOKEN", None)
    c1 = auth.codigo_instalacion()
    assert re.fullmatch(r"([0-9A-F]{4}-){2}[0-9A-F]{4}", c1) and auth.codigo_instalacion() == c1


def test_setup_ip_rate_limit(basic):
    tok = _csrf(basic.get("/setup").text)
    data = {"usuario": "admin", "clave": PW, "clave2": PW, "codigo": "MAL", "csrf": tok}
    codes = [basic.post("/setup", data=data).status_code for _ in range(12)]
    assert codes[-1] == 429


# ---------------- login / logout ----------------
def test_login_flow_next_and_open_redirect(admin):
    admin.post("/logout", headers=hx(admin))
    r = admin.get("/lista?estado=favoritas")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    r = login(admin, "admin", PW, "/lista?estado=favoritas")
    assert r.status_code == 303 and r.headers["location"] == "/lista?estado=favoritas"
    admin.post("/logout", headers=hx(admin))
    for evil in ["//evil.com", "https://evil.com", "/\\evil.com"]:
        assert login(admin, "admin", PW, evil).headers["location"] == "/", evil
        admin.post("/logout", headers=hx(admin))


def test_wrong_credentials_are_indistinguishable(admin):
    admin.post("/logout", headers=hx(admin))
    bad_pw = login(admin, "admin", "mala-clave-larga-1")
    no_user = login(admin, "nadie", "mala-clave-larga-1")
    assert bad_pw.status_code == no_user.status_code == 401
    msg = lambda r: re.search(r'role="alert">([^<]+)<', r.text).group(1)  # noqa: E731
    assert msg(bad_pw) == msg(no_user)


def test_login_requires_csrf_cookie(admin):
    admin.post("/logout", headers=hx(admin))
    admin.get("/login")
    r = admin.post("/login", data={"usuario": "admin", "clave": PW, "csrf": "falso"})
    assert r.status_code == 400
    r = new_client().post("/login", data={"usuario": "admin", "clave": PW, "csrf": "x"})   # sin cookie previa
    assert r.status_code == 400


def test_lockout_after_failures_even_with_right_password(admin):
    from sqlalchemy.orm import Session
    from app.main import ENGINE
    from app.models_web import Cuenta
    admin.post("/logout", headers=hx(admin))
    for _ in range(5):
        assert login(admin, "admin", "incorrecta-larga-1").status_code == 401
    assert login(admin, "admin", PW).status_code == 401                     # bloqueada: ni la clave correcta entra
    with Session(ENGINE) as s:
        c = s.query(Cuenta).one()
        assert c.bloqueado_hasta > datetime.now() and c.fallos == 5
        c.bloqueado_hasta = datetime.now() - timedelta(seconds=1)
        s.commit()
    assert login(admin, "admin", PW).status_code == 303                     # vencido el bloqueo, vuelve a entrar
    with Session(ENGINE) as s:
        assert s.query(Cuenta).one().fallos == 0


def test_login_ip_rate_limit(admin):
    admin.post("/logout", headers=hx(admin))
    codes = [login(admin, "nadie", "x-clave-larga-1234").status_code for _ in range(12)]
    assert 429 in codes


def test_logout_invalidates_the_session(admin):
    name = "inmo_session"
    old = admin.cookies.get(name)
    assert old and admin.get("/").status_code == 200
    admin.post("/logout", headers=hx(admin))
    admin.cookies.set(name, old)                                             # reusar la cookie vieja
    assert admin.get("/").status_code == 303


def test_idle_and_absolute_expiry(admin, monkeypatch):
    monkeypatch.setattr("app.config.SESSION_IDLE_HOURS", 0)
    assert admin.get("/").status_code == 303
    monkeypatch.setattr("app.config.SESSION_IDLE_HOURS", 8)
    login(admin, "admin", PW)
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from app.main import ENGINE
    from app.models_web import Sesion
    with Session(ENGINE) as s:
        s.execute(update(Sesion).values(expira=datetime.now() - timedelta(seconds=1)))
        s.commit()
    assert admin.get("/").status_code == 303


def test_secrets_are_not_stored_in_clear(admin):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app.main import ENGINE
    token = admin.cookies.get("inmo_session")
    with Session(ENGINE) as s:
        assert s.execute(text("SELECT password_hash FROM web_cuentas")).scalar().startswith("$argon2id$")
        assert token not in s.execute(text("SELECT id_hash FROM web_sesiones")).scalar()
        assert PW not in str(s.execute(text("SELECT * FROM web_cuentas")).all())


def test_https_uses_host_prefixed_secure_cookie_and_hsts(basic_https):
    r = setup_admin(basic_https)
    cookie = r.headers.get_list("set-cookie")[0]
    assert cookie.startswith("__Host-inmo_session=") and "Secure" in cookie and "HttpOnly" in cookie
    resp = basic_https.get("/lista")
    assert "max-age" in resp.headers["strict-transport-security"] and resp.headers["cache-control"] == "no-store"


# ---------------- CSRF ----------------
def test_csrf_token_required_for_state_changes(admin):
    url, data = "/p/1/accion", {"accion": "favorito", "vista": "fila"}
    assert admin.post(url, data=data, headers={"HX-Request": "true"}).status_code == 403           # sin token
    assert admin.post(url, data=data, headers={"HX-Request": "true", "X-CSRF-Token": "falso"}).status_code == 403
    good = hx(admin)
    assert admin.post(url, data=data, headers={"X-CSRF-Token": good["X-CSRF-Token"]}).status_code == 403   # sin HX-Request
    assert admin.post(url, data=data, headers=good).status_code == 200
    other = new_client()                                                        # el token de otra sesión no sirve
    setup_ok = login(other, "admin", PW)
    assert setup_ok.status_code == 303
    assert other.post(url, data=data, headers={"HX-Request": "true", "X-CSRF-Token": good["X-CSRF-Token"]}).status_code == 403


def test_htmx_gets_redirect_header_when_session_lost(admin):
    admin.post("/logout", headers=hx(admin))
    r = admin.get("/scrapper/estado", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["hx-redirect"] == "/login"
    assert admin.get("/api/mapa").status_code == 401


# ---------------- cuenta y usuarios ----------------
def _crear(admin, usuario, rol="usuario", clave=""):
    return admin.post("/usuarios/crear", data={"usuario": usuario, "clave": clave, "rol": rol}, headers=hx(admin))


def _temp(html):
    return re.search(r'user-select:all">([^<]+)<', html).group(1)


def test_admin_creates_user_who_must_change_password(admin):
    r = _crear(admin, "beto")
    assert r.status_code == 200 and "creado" in r.text
    temp = _temp(r.text)
    assert len(temp) >= 16
    beto = new_client()
    assert login(beto, "beto", temp).headers["location"] == "/cuenta"          # va directo a cambiarla
    assert beto.get("/lista").headers["location"] == "/cuenta"                 # y no puede usar la app antes
    h = hx(beto)
    assert "no es correcta" in beto.post("/cuenta/clave", data={"actual": "x", "nueva": PW, "nueva2": PW}, headers=h).text
    assert "al menos 12" in beto.post("/cuenta/clave", data={"actual": temp, "nueva": "corta", "nueva2": "corta"}, headers=h).text
    r = beto.post("/cuenta/clave", data={"actual": temp, "nueva": "Otra-frase-larga-77", "nueva2": "Otra-frase-larga-77"}, headers=h)
    assert r.status_code == 200 and r.headers["hx-redirect"] == "/" and "actualizada" in r.text
    assert beto.get("/lista").status_code == 200
    assert login(new_client(), "beto", temp).status_code == 401                # la temporal ya no sirve


def test_only_admin_can_manage_users(admin):
    temp = _temp(_crear(admin, "beto").text)
    beto = new_client()
    login(beto, "beto", temp)
    beto.post("/cuenta/clave", data={"actual": temp, "nueva": "Otra-frase-larga-77", "nueva2": "Otra-frase-larga-77"}, headers=hx(beto))
    assert beto.get("/usuarios").status_code == 403
    assert beto.post("/usuarios/crear", data={"usuario": "x1x", "rol": "admin"}, headers=hx(beto)).status_code == 403
    assert "Usuarios</a>" not in beto.get("/estado").text                       # ni siquiera ve el enlace
    assert "Usuarios</a>" in admin.get("/estado").text


def test_user_admin_rules(admin):
    assert "Ya existe" in _crear(admin, "admin").text
    assert "Usuario inválido" in _crear(admin, "Mal Nombre!").text
    assert "al menos 12" in _crear(admin, "beto", clave="corta").text
    assert "Rol inválido" in _crear(admin, "beto", rol="root").text
    _crear(admin, "beto")
    from sqlalchemy.orm import Session
    from app.main import ENGINE
    from app.models_web import Cuenta
    with Session(ENGINE) as s:
        yo = s.query(Cuenta).filter_by(username="admin").one().id
        beto_id = s.query(Cuenta).filter_by(username="beto").one().id
    assert "propia cuenta" in admin.post(f"/usuarios/{yo}/activo", headers=hx(admin)).text
    assert "al menos un administrador" in admin.post(f"/usuarios/{yo}/rol", headers=hx(admin)).text
    admin.post(f"/usuarios/{beto_id}/rol", headers=hx(admin))                    # beto → admin
    with Session(ENGINE) as s:
        assert s.get(Cuenta, beto_id).rol == "admin"
    assert "al menos un administrador" not in admin.post(f"/usuarios/{yo}/rol", headers=hx(admin)).text   # ahora sí puede


def test_disable_kills_sessions_and_reset_password(admin):
    temp = _temp(_crear(admin, "beto").text)
    beto = new_client()
    login(beto, "beto", temp)
    beto.post("/cuenta/clave", data={"actual": temp, "nueva": "Otra-frase-larga-77", "nueva2": "Otra-frase-larga-77"}, headers=hx(beto))
    assert beto.get("/lista").status_code == 200
    from sqlalchemy.orm import Session
    from app.main import ENGINE
    from app.models_web import Cuenta
    with Session(ENGINE) as s:
        bid = s.query(Cuenta).filter_by(username="beto").one().id
    admin.post(f"/usuarios/{bid}/activo", headers=hx(admin))
    assert beto.get("/lista").status_code == 303                                   # su sesión murió
    assert login(new_client(), "beto", "Otra-frase-larga-77").status_code == 401   # y no puede volver a entrar
    admin.post(f"/usuarios/{bid}/activo", headers=hx(admin))                       # reactivar
    nueva = _temp(admin.post(f"/usuarios/{bid}/clave", headers=hx(admin)).text)
    assert login(new_client(), "beto", "Otra-frase-larga-77").status_code == 401   # la anterior ya no vale
    assert login(new_client(), "beto", nueva).headers["location"] == "/cuenta"


def test_password_change_closes_other_sessions(admin):
    other = new_client()
    assert login(other, "admin", PW).status_code == 303
    assert other.get("/lista").status_code == 200
    r = admin.post("/cuenta/clave", data={"actual": PW, "nueva": "Nueva-frase-larga-55", "nueva2": "Nueva-frase-larga-55"}, headers=hx(admin))
    assert "actualizada" in r.text
    assert admin.get("/lista").status_code == 200        # la sesión actual sigue
    assert other.get("/lista").status_code == 303        # las demás se cerraron


# ---------------- otros modos ----------------
def test_auth_pages_do_not_exist_in_other_modes(client):
    assert client.get("/login").headers["location"] == "/"
    assert client.get("/setup").headers["location"] == "/"
    assert client.get("/usuarios").status_code == 404
    assert client.get("/cuenta").status_code == 404
    assert "csrf-token" not in client.get("/lista").text


# ---------------- primitivas ----------------
def test_security_primitives():
    from app import security as sec
    assert sec.validar_clave("Correct-horse-battery-9", "admin") is None
    assert sec.validar_clave("a" * 129) and sec.validar_clave("aaaaaaaaaaaaaa") and sec.validar_clave("password1234")
    assert sec.validar_usuario("ab") and sec.validar_usuario("Admin") and sec.validar_usuario("ana.perez-1") is None
    assert sec.next_seguro("/lista?x=1") == "/lista?x=1" and sec.next_seguro("/login") == "/" and sec.next_seguro(None) == "/"
    h = sec.hash_clave("clave-de-prueba-123")
    assert sec.verificar_clave(h, "clave-de-prueba-123") and not sec.verificar_clave(h, "otra") and not sec.verificar_clave("basura", "x")
    lim = sec.Limitador(2, 60)
    assert [lim.permitir("a"), lim.permitir("a"), lim.permitir("a"), lim.permitir("b")] == [True, True, False, True]
    g = sec.generar_clave()
    assert len(g) == 16 and sec.validar_clave(g) is None


def test_forwarded_for_spoofing_does_not_bypass_ip_limit(admin, monkeypatch):
    monkeypatch.setattr("app.config.TRUSTED_PROXY_HOPS", 1)
    admin.post("/logout", headers=hx(admin))
    codes = []
    for i in range(12):                        # el cliente inventa una IP distinta en cada intento
        tok = _csrf(admin.get("/login").text)
        r = admin.post("/login", data={"usuario": "nadie", "clave": "x-clave-larga-1234", "csrf": tok},
                       headers={"X-Forwarded-For": f"10.9.8.{i}, 203.0.113.7"})   # la derecha la agrega el proxy
        codes.append(r.status_code)
    assert codes[-1] == 429


def test_client_ip_and_scheme_from_trusted_hops(admin, monkeypatch):
    import app.auth as auth

    class Req:
        def __init__(self, h): self.headers, self.client = h, type("C", (), {"host": "172.18.0.5"})()
    monkeypatch.setattr("app.config.TRUSTED_PROXY_HOPS", 1)
    assert auth.client_ip(Req({"x-forwarded-for": "6.6.6.6, 203.0.113.7"})) == "203.0.113.7"
    assert auth.client_ip(Req({})) == "172.18.0.5"
    monkeypatch.setattr("app.config.TRUSTED_PROXY_HOPS", 0)
    assert auth.client_ip(Req({"x-forwarded-for": "6.6.6.6"})) == "172.18.0.5"      # sin proxy: se ignora la cabecera
