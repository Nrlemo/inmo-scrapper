"""Bases separadas (compra / alquiler) y autenticación compartida (Etapa 3)."""
import re
from datetime import datetime
from pathlib import Path

import pytest

PW = "Correct-horse-battery-9"
CODE = "TEST-CODE-1234"


def _csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _hx(c):
    meta = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/cuenta").text).group(1)
    return {"HX-Request": "true", "X-CSRF-Token": meta}


@pytest.fixture()
def admin(basic):
    tok = _csrf(basic.get("/setup").text)
    r = basic.post("/setup", data={"usuario": "admin", "clave": PW, "clave2": PW, "codigo": CODE, "csrf": tok})
    assert r.status_code == 303
    return basic


def _login(c, base=None):
    tok = _csrf(c.get("/login").text)
    data = {"usuario": "admin", "clave": PW, "next": "/", "csrf": tok}
    if base:
        data["base"] = base
    return c.post("/login", data=data)


def test_cada_base_tiene_su_archivo(client):
    from app import config
    assert len({config.DB_PATH, config.DB_PATH_ALQUILER, config.DB_PATH_AUTH}) == 3
    for ruta in (config.DB_PATH, config.DB_PATH_ALQUILER, config.DB_PATH_AUTH):
        assert Path(ruta).is_file(), ruta


def test_las_cuentas_no_estan_en_las_bases_de_avisos(admin):
    from sqlalchemy import text
    from app.main import AUTH_ENGINE, ENGINE
    with AUTH_ENGINE.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM web_cuentas")).scalar_one() == 1
    for motor in (ENGINE, admin.app.state.engines["alquiler"]):
        with motor.connect() as c:
            assert not c.execute(text("SELECT 1 FROM sqlite_master WHERE name IN ('web_cuentas', 'web_sesiones', "
                                      "'web_tokens')")).first()


# ---------------- migración de cuentas, sesiones y tokens ----------------
def _base_vieja(tmp_path):
    """Base de compra anterior a la separación: cuentas, sesiones (sin columna base) y tokens dentro de ella."""
    from sqlalchemy import create_engine, text
    motor = create_engine(f"sqlite:///{tmp_path / 'vieja.sqlite'}")
    with motor.begin() as c:
        c.execute(text("CREATE TABLE web_cuentas (id INTEGER PRIMARY KEY, username VARCHAR(32), password_hash VARCHAR(255), "
                       "rol VARCHAR(10), activo BOOLEAN, debe_cambiar BOOLEAN, creado DATETIME, ultimo_login DATETIME, "
                       "fallos INTEGER, bloqueado_hasta DATETIME)"))
        c.execute(text("CREATE TABLE web_sesiones (id_hash VARCHAR(64) PRIMARY KEY, cuenta_id INTEGER, csrf VARCHAR(64), "
                       "creada DATETIME, actividad DATETIME, expira DATETIME, ip VARCHAR(45), agente VARCHAR(200))"))
        c.execute(text("CREATE TABLE web_tokens (id INTEGER PRIMARY KEY, usuario VARCHAR(128), hash VARCHAR(64), "
                       "creado DATETIME, ultimo_uso DATETIME)"))
        c.execute(text("INSERT INTO web_cuentas VALUES (7, 'ana', 'h', 'admin', 1, 0, '2026-01-01 00:00:00', NULL, 0, NULL)"))
        c.execute(text("INSERT INTO web_sesiones VALUES ('s1', 7, 'c', '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                       "'2099-01-01 00:00:00', NULL, NULL)"))
        c.execute(text("INSERT INTO web_tokens VALUES (3, 'ana', 'hashdeltoken', '2026-01-01 00:00:00', NULL)"))
    return motor


def test_migracion_copia_una_sola_vez_y_no_borra_lo_viejo(client, tmp_path):
    from sqlalchemy import text
    from app import db
    vieja = _base_vieja(tmp_path)
    auth = db._motor_auth(str(tmp_path / "auth-nueva.sqlite"))
    assert db.migrar_auth(auth, vieja) == {"web_cuentas": 1, "web_sesiones": 1, "web_tokens": 1}
    with auth.connect() as c:
        assert c.execute(text("SELECT id, username, rol FROM web_cuentas")).one() == (7, "ana", "admin")
        assert c.execute(text("SELECT cuenta_id, base, recordar FROM web_sesiones")).one() == (7, "compra", 0)
        assert c.execute(text("SELECT id, usuario FROM web_tokens")).one() == (3, "ana")
    with vieja.connect() as c:                                    # lo viejo queda: se puede volver a la versión anterior
        assert c.execute(text("SELECT COUNT(*) FROM web_cuentas")).scalar_one() == 1
    with auth.begin() as c:                                       # se borra un token en la base nueva...
        c.execute(text("DELETE FROM web_tokens"))
    assert db.migrar_auth(auth, vieja) == {}                      # ...y no resucita en el próximo arranque
    with auth.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM web_tokens")).scalar_one() == 0


def test_migracion_sin_tablas_viejas_no_falla(client, tmp_path):
    from sqlalchemy import create_engine
    from app import db
    auth = db._motor_auth(str(tmp_path / "auth-nueva.sqlite"))
    vacia = create_engine(f"sqlite:///{tmp_path / 'vacia.sqlite'}")
    assert db.migrar_auth(auth, vacia) == {}
    assert db.migrar_auth(auth, vacia) == {}


# ---------------- elegir y cambiar de base ----------------
def test_login_sin_base_entra_a_compra(admin):
    admin.post("/logout", headers=_hx(admin))
    assert _login(admin).status_code == 303
    assert "Calle 1" in admin.get("/lista").text


def test_login_en_alquiler_ve_una_base_vacia_y_se_puede_volver(admin):
    admin.post("/logout", headers=_hx(admin))
    assert _login(admin, "alquiler").status_code == 303
    assert "Calle 1" not in admin.get("/lista").text                # los avisos de venta no se mezclan
    r = admin.post("/base", data={"base": "compra"}, headers=_hx(admin))
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/"
    assert "Calle 1" in admin.get("/lista").text
    admin.post("/base", data={"base": "inventada"}, headers=_hx(admin))   # lo desconocido cae en compra
    assert "Calle 1" in admin.get("/lista").text


def test_cambiar_de_base_exige_csrf(admin):
    assert admin.post("/base", data={"base": "alquiler"}).status_code == 403


def test_una_marca_en_alquiler_no_toca_compra(admin):
    from sqlalchemy import text
    admin.post("/base", data={"base": "alquiler"}, headers=_hx(admin))
    from app.main import ENGINE
    from sqlalchemy.orm import Session
    from inmo.models import Categorizacion, Publicacion
    now = datetime.now()
    with Session(admin.app.state.engines["alquiler"]) as s:
        p = Publicacion(portal="zonaprop", id_externo="a1", url="https://x/a1", direccion="Calle Alquiler", precio=500000,
                        moneda="ARS", ambientes=2, fotos=[], fecha_primera_vista=now, fecha_ultima_vista=now,
                        activa=True, consultas_sin_ver=0)
        p.categorizacion = Categorizacion(estado="nuevo", etiquetas=[], fecha_modificacion=now)
        s.add(p)
        s.commit()
    assert "Calle Alquiler" in admin.get("/lista").text
    admin.post("/base", data={"base": "compra"}, headers=_hx(admin))
    assert "Calle Alquiler" not in admin.get("/lista").text
    with ENGINE.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM publicaciones")).scalar_one() == 3


def test_api_login_acepta_base_y_sin_ella_es_compra(admin):
    r = admin.post("/api/login", json={"usuario": "admin", "clave": PW})
    assert r.status_code == 200 and r.json()["base"] == "compra"
    r = admin.post("/api/login", json={"usuario": "admin", "clave": PW, "base": "alquiler"})
    assert r.json()["base"] == "alquiler"
    assert admin.post("/api/login", json={"usuario": "admin", "clave": PW, "base": "x"}).json()["base"] == "compra"


def test_sin_sesiones_propias_la_base_se_recuerda_en_una_cookie(client):
    assert "Calle 1" in client.get("/lista").text
    r = client.post("/base", data={"base": "alquiler"}, headers={"HX-Request": "true"})
    assert r.status_code == 204 and "inmo_base=alquiler" in r.headers["set-cookie"]
    assert "Calle 1" not in client.get("/lista").text


# ---------------- API de la extensión ----------------
def _token(client):
    r = client.post("/navegador/token", headers={"HX-Request": "true"})
    return {"Authorization": "Bearer " + re.search(r'id="tok-nuevo">([^<]+)<', r.text).group(1)}


def test_api_ronda_sin_base_es_de_compra_y_con_base_va_a_su_base(client):
    from sqlalchemy import text
    auth = _token(client)
    r = client.post("/api/navegador/ronda", headers=auth, json={"base": "alquiler"}).json()
    assert "omitir" in r                                           # alquiler todavía no tiene búsquedas activas
    with client.app.state.engines["alquiler"].connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM ejecuciones")).scalar_one() == 0
    r = client.post("/api/navegador/ronda", headers=auth).json()   # extensión 1.1.0: no manda base
    assert r["ronda"]
    with client.app.state.engines["compra"].connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM ejecuciones")).scalar_one() == 1
    # la ronda es de compra: en la base de alquiler no existe
    assert client.post("/api/navegador/cancelar", headers=auth,
                       json={"base": "alquiler", "ronda": r["ronda"]}).json()["fin"] is True
    assert client.post("/api/navegador/cancelar", headers=auth, json={"ronda": r["ronda"]}).json()["estado"] == "cancelada"


def test_filtros_de_alquiler_arrancan_vacios_y_son_de_alquiler(client):
    from app import busqueda
    from sqlalchemy.orm import Session
    with Session(client.app.state.engines["alquiler"], info={"base": "alquiler"}) as s:
        doc = busqueda.obtener(s)                                  # no importa los perfiles de compra de profiles.yaml
        b = doc["busquedas"][0]
        assert b["nombre"] == "alquiler" and b["comunes"]["operacion"] == "alquiler" and b["comunes"]["moneda"] == "ARS"
        assert not any(pc["activo"] for pc in b["portales"].values())
        b["comunes"]["operacion"] = "compra"
        with pytest.raises(ValueError, match="alquiler"):
            busqueda.guardar(s, doc, "ana")


def test_no_se_corren_rondas_de_las_dos_bases_a_la_vez(client):
    from sqlalchemy.orm import Session
    from inmo.models import Ejecucion
    auth = _token(client)
    ahora = datetime.now()
    with Session(client.app.state.engines["alquiler"]) as s:
        s.add(Ejecucion(portal="zonaprop", usuario="ana (navegador)", inicio=ahora, actualizado=ahora, estado="corriendo"))
        s.commit()
    r = client.post("/api/navegador/ronda", headers=auth).json()          # compra, mientras alquiler está corriendo
    assert "otra base" in r["omitir"]


def test_pantallas_en_la_base_de_alquiler(admin):
    admin.post("/base", data={"base": "alquiler"}, headers=_hx(admin))
    for url in ["/", "/lista", "/lista?moneda=ARS&pmin=1", "/lista?orden=usd_m2", "/ranking", "/cambios", "/comparar", "/mapa",
                "/estado"]:
        r = admin.get(url)
        assert r.status_code == 200, url
    estado = admin.get("/estado").text
    assert "Alquiler · Departamento" in estado and 'name="expensas_max"' in estado and 'name="apto_credito"' not in estado
    assert 'class="badge alq"' in admin.get("/lista").text and 'name="moneda"' in admin.get("/lista").text
    admin.post("/base", data={"base": "compra"}, headers=_hx(admin))
    estado = admin.get("/estado").text
    assert "Compra · Departamento · USD" in estado and 'name="expensas_max"' not in estado
    assert 'class="badge alq"' not in admin.get("/lista").text


def test_el_indicador_de_barrio_usa_la_moneda_de_cada_base(admin):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from inmo.models import Categorizacion, Publicacion
    from app import mercado
    ahora = datetime.now()
    alq, compra = admin.app.state.engines["alquiler"], admin.app.state.engines["compra"]
    with Session(alq) as s:
        for i, precio in enumerate([400000, 500000, 600000, 700000, 2000000], 1):
            p = Publicacion(portal="zonaprop", id_externo=f"r{i}", url="u", barrio="Almagro", precio=precio, moneda="ARS",
                            ambientes=2, m2_cubiertos=50, fotos=[], fecha_primera_vista=ahora, fecha_ultima_vista=ahora,
                            activa=True, consultas_sin_ver=0)
            p.categorizacion = Categorizacion(estado="nuevo", etiquetas=[], fecha_modificacion=ahora)
            s.add(p)
        s.commit()
    with alq.connect() as c:
        assert mercado.actualizar(c, base="alquiler")                    # 5 avisos en pesos: hay referencia
        assert c.execute(text("SELECT COUNT(*) FROM web_vs_barrio")).scalar_one() == 5
    with compra.connect() as c:                                          # compra: 3 avisos en USD, muestra chica
        mercado.actualizar(c, base="compra")
        assert c.execute(text("SELECT COUNT(*) FROM web_vs_barrio")).scalar_one() == 0
    with Session(alq) as s:                                              # los avisos en USD no entran en la de alquiler
        assert s.execute(text("UPDATE publicaciones SET moneda='USD'")).rowcount == 5
        s.commit()
    with alq.connect() as c:
        mercado.actualizar(c, forzar=True, base="alquiler")      # (la firma de datos no cambia con sólo mover la moneda)
        assert c.execute(text("SELECT COUNT(*) FROM web_vs_barrio")).scalar_one() == 0


def test_etiquetas_de_alquiler_se_suman_solo_en_esa_base(client, tmp_path):
    from sqlalchemy.orm import Session
    from app import busqueda, config
    Path(config.CONFIG_PATH).write_text(
        "profiles:\n  - name: p\n    portals:\n      zonaprop:\n        search_url_template: x\n        zones: [almagro]\n"
        "auto_tags:\n  patio: [patio]\n  mascotas: [mascotas]\nauto_tags_compra:\n  apto_credito: [credito]\nauto_tags_alquiler:\n  amoblado: [amoblado]\n  mascotas: [pet]\n")
    with Session(client.app.state.engines["compra"], info={"base": "compra"}) as s:
        assert set(busqueda.config_efectiva(s)["auto_tags"]) == {"patio", "mascotas", "apto_credito"}
    with Session(client.app.state.engines["alquiler"], info={"base": "alquiler"}) as s:
        tags = busqueda.config_efectiva(s)["auto_tags"]
        assert set(tags) == {"patio", "mascotas", "amoblado"} and tags["mascotas"] == ["pet"]
