from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo.connectors.base import Listing
from inmo.connectors.common import _money, _num, matches_profile
from inmo.connectors.zonaprop import page_url, parse_listing_page, parse_pictures
from inmo.models import Publicacion, make_engine
from inmo import repo

HTML = (Path(__file__).parent / "fixtures" / "zonaprop_listado.html").read_text(encoding="utf-8")


def test_numbers_and_money():
    assert _num("215.000") == 215000 and _num("76,35") == 76.35 and _num("84") == 84
    assert _money("USD 215.000") == (215000, "USD")
    assert _money("$ 300.000 Expensas") == (300000, "ARS")
    assert _money(None) == (None, None)


def test_parse_page():
    listings, total = parse_listing_page(HTML)
    assert total == 2864
    assert 20 <= len(listings) <= 30                      # emprendimientos omitidos
    assert len({l.id_externo for l in listings}) == len(listings)
    l = next(x for x in listings if x.id_externo == "58402478")
    assert l.url == "https://www.zonaprop.com.ar/propiedades/clasificado/veclapin-echeverria-al-5200-58402478.html"
    assert (l.precio, l.moneda) == (215000, "USD")
    assert (l.expensas, l.moneda_expensas) == (300000, "ARS")
    assert (l.m2_totales, l.ambientes, l.dormitorios, l.banos) == (84, 3, 2, 2)
    assert l.m2_cubiertos == 76.35                         # de "Superficie cubierta: 76, 35 m²"
    assert l.direccion == "Echeverría al 5200" and l.barrio == "Villa Urquiza"
    assert l.fotos and l.fotos[0].startswith("https://imgar.zonapropcdn.com/avisos/")
    assert "logo" not in l.fotos[0]
    # galería: todas las fotos del estado precargado, en 720x532, empezando por la de la tarjeta
    assert len(l.fotos) == 8 and all("/720x532/" in f for f in l.fotos) and len(set(l.fotos)) == 8
    assert l.fotos[0].split("/")[-1].startswith("2071416452.jpg")
    assert all(len(x.fotos) >= 1 for x in listings)
    c = next(x for x in listings if x.id_externo == "60010042")
    assert c.cocheras == 1 and c.m2_totales == 122 and c.ambientes == 4
    assert all(x.precio and x.moneda for x in listings)


def test_page_url():
    u = "https://www.zonaprop.com.ar/departamentos-venta-x.html"
    assert page_url(u, 1) == u
    assert page_url(u, 3) == "https://www.zonaprop.com.ar/departamentos-venta-x-pagina-3.html"


def test_matches_profile():
    prof = {"currency": "USD", "price": {"min": 50000, "max": 125000}, "rooms": {"min": 3},
            "total_m2_min": 65, "exclude_keywords": ["pozo", "sin escritura"]}
    ok = Listing("zonaprop", "1", "u", titulo="Depto", precio=100000, moneda="USD", ambientes=3, m2_totales=70)
    assert matches_profile(ok, prof)
    assert not matches_profile(Listing("zonaprop", "2", "u", precio=130000, moneda="USD"), prof)
    assert not matches_profile(Listing("zonaprop", "3", "u", precio=100000, moneda="USD", ambientes=2), prof)
    assert not matches_profile(Listing("zonaprop", "4", "u", precio=100000, moneda="USD", m2_totales=50), prof)
    assert not matches_profile(Listing("zonaprop", "5", "u", precio=100000, moneda="USD", descripcion="Emprendimiento en POZO"), prof)
    assert matches_profile(Listing("zonaprop", "6", "u", precio=100000, moneda="USD"), prof)  # sin m² => no se descarta


@pytest.fixture
def engine(tmp_path):
    return make_engine(str(tmp_path / "t.sqlite"))


def test_upsert_price_history_and_inactivation(engine):
    t0 = datetime(2026, 9, 20, 10)
    with Session(engine) as s:
        p, kind = repo.upsert(s, Listing("zonaprop", "1", "u", precio=100000, moneda="USD"), t0)
        assert kind == "nueva" and p.categorizacion.estado == "nuevo"
        _, kind = repo.upsert(s, Listing("zonaprop", "1", "u", precio=100000, moneda="USD"), t0 + timedelta(days=1))
        assert kind == "vista" and p.fecha_ultima_vista == t0 + timedelta(days=1)
        _, kind = repo.upsert(s, Listing("zonaprop", "1", "u", precio=95000, moneda="USD"), t0 + timedelta(days=2))
        assert kind == "precio_cambiado" and [h.precio for h in p.historial] == [100000, 95000]
        assert [h.variacion_pct for h in p.historial] == [None, -5.0]
        repo.upsert(s, Listing("zonaprop", "1", "u", precio=104500, moneda="USD"), t0 + timedelta(days=3))
        assert p.historial[-1].variacion_pct == 10.0
        del p.historial[-1]
        p.precio = 95000
        for _ in range(3):
            repo.mark_missing(s, "zonaprop", set(), 3)
        assert p.activa is False
        repo.upsert(s, Listing("zonaprop", "1", "u", precio=95000, moneda="USD"), t0 + timedelta(days=9))
        assert p.activa is True and p.consultas_sin_ver == 0
        s.commit()
        assert s.scalar(select(Publicacion).where(Publicacion.id_externo == "1")) is p


def test_cross_portal_link(engine):
    with Session(engine) as s:
        now = datetime(2026, 9, 20)
        a, _ = repo.upsert(s, Listing("zonaprop", "1", "u", direccion="Echeverría al 5200", barrio="Villa Urquiza",
                                      precio=215000, moneda="USD", m2_totales=84), now)
        b, _ = repo.upsert(s, Listing("argenprop", "9", "u", direccion="Echeverria 5200", barrio="Villa Urquiza",
                                      precio=213000, moneda="USD", m2_totales=85), now)
        assert a.grupo_id == b.grupo_id == a.id


def test_search_urls_explicitas():
    from inmo.connectors.zonaprop import search_urls
    prof = {"portals": {"zonaprop": {"search_urls": ["https://www.zonaprop.com.ar/x-y.html"]}}}
    assert search_urls(prof) == [("x-y", "https://www.zonaprop.com.ar/x-y.html")]


def test_migration_adds_and_backfills_variacion(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.sqlite")
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE historial_precios (id INTEGER PRIMARY KEY, publicacion_id INTEGER,
        precio FLOAT, moneda VARCHAR(3), fecha DATETIME);
        INSERT INTO historial_precios VALUES (1,1,100000,'USD','2026-09-01'),(2,1,90000,'USD','2026-09-05'),
                                             (3,2,50000,'USD','2026-09-01');""")
    con.commit(); con.close()
    make_engine(path)
    rows = sqlite3.connect(path).execute("SELECT id, variacion_pct FROM historial_precios ORDER BY id").fetchall()
    assert rows == [(1, None), (2, -10.0), (3, None)]


def test_publisher_parsing():
    from inmo.connectors.zonaprop import parse_corredor, parse_publisher
    base = "https://imgar.zonapropcdn.com/empresas/1/00/"
    assert parse_publisher(base + "17/60/77/09/130x70/logo_remax-premium-ii_1769536932014.jpg") == ("10017607709", "remax premium ii")
    assert parse_publisher(base + "30/68/37/54/130x70/logo_ava_1751300575492.jpg") == ("10030683754", "ava")
    assert parse_publisher(base + "17/02/43/73/130x70/logo_verges-cuevas-propiedades_2.jpg")[1] == "verges cuevas propiedades"
    assert parse_publisher(None) == (None, None) and parse_publisher("https://x/y.jpg") == (None, None)
    assert parse_corredor("x Corredor Inmobiliario: Mariano Rico Alcázar cpi 1984 / comado 1526Nota importante: Toda") \
        == "Mariano Rico Alcázar cpi 1984 / comado 1526"
    assert parse_corredor("Corredor Responsable: Ariel Champanier cucicba 4330 / Andrea Berre cmcpsi 6763- Contacto: Alberto") \
        == "Ariel Champanier cucicba 4330 / Andrea Berre cmcpsi 6763"
    assert parse_corredor("sin datos") is None


def test_fixture_all_cards_have_publisher():
    listings, _ = parse_listing_page(HTML)
    assert all(l.inmobiliaria_id_externo and l.inmobiliaria_nombre for l in listings)
    assert next(l for l in listings if l.id_externo == "58402478").inmobiliaria_nombre == "ava"
    assert len({l.inmobiliaria_id_externo for l in listings}) > 15


def test_one_inmobiliaria_for_two_listings(engine):
    from inmo.models import Inmobiliaria
    kw = dict(inmobiliaria_id_externo="10030683754", inmobiliaria_nombre="ava", inmobiliaria_logo="u1")
    with Session(engine) as s:
        a, _ = repo.upsert(s, Listing("zonaprop", "1", "u", **kw), datetime(2026, 9, 20))
        b, _ = repo.upsert(s, Listing("zonaprop", "2", "u", corredor="Ana cpi 1", **kw), datetime(2026, 9, 20))
        s.commit()
        assert a.inmobiliaria_id == b.inmobiliaria_id and len(s.scalars(select(Inmobiliaria)).all()) == 1
        assert b.corredor == "Ana cpi 1"
        # aviso ya existente sin inmobiliaria (guardado antes de esta versión) la recibe al reaparecer
        c, _ = repo.upsert(s, Listing("zonaprop", "3", "u"), datetime(2026, 9, 20))
        assert c.inmobiliaria_id is None
        repo.upsert(s, Listing("zonaprop", "3", "u", **kw), datetime(2026, 9, 21))
        assert c.inmobiliaria_id == a.inmobiliaria_id


def test_migration_adds_inmobiliaria_columns(tmp_path):
    import sqlite3
    path = str(tmp_path / "old2.sqlite")
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE publicaciones (id INTEGER PRIMARY KEY, portal TEXT, id_externo TEXT, url TEXT);"
                      "INSERT INTO publicaciones VALUES (1,'zonaprop','1','u');")
    con.commit(); con.close()
    make_engine(path)
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(publicaciones)")}
    assert {"inmobiliaria_id", "corredor"} <= cols
    assert con.execute("SELECT name FROM sqlite_master WHERE name='inmobiliarias'").fetchone()


def test_zone_split_by_price():
    from inmo.connectors.zonaprop import search_urls
    prof = {"price": {"min": 50000, "max": 125000}, "portals": {"zonaprop": {
        "search_url_template": "https://x/d-{zone}-{price_min}-{price_max}-dolar.html",
        "zones": [{"zone": "almagro", "price_max": 110000}, {"zone": "almagro", "price_min": 110000}, "boedo"]}}}
    assert search_urls(prof) == [
        ("almagro 50000-110000", "https://x/d-almagro-50000-110000-dolar.html"),
        ("almagro 110000-125000", "https://x/d-almagro-110000-125000-dolar.html"),
        ("boedo", "https://x/d-boedo-50000-125000-dolar.html")]


def test_geolocation_parsed():
    listings, _ = parse_listing_page(HTML)
    l = next(x for x in listings if x.id_externo == "58402478")
    assert (l.lat, l.lng) == (-34.580485, -58.4833206)
    assert all(-35 < x.lat < -34 and -59 < x.lng < -58 for x in listings if x.lat is not None)


def test_cross_portal_link_tolerates_barrio_naming(engine):
    with Session(engine) as s:
        now = datetime(2026, 9, 20)
        a, _ = repo.upsert(s, Listing("zonaprop", "1", "u", direccion="Yatay 760", barrio="Almagro Norte",
                                      precio=87000, moneda="USD", m2_totales=55), now)
        b, _ = repo.upsert(s, Listing("argenprop", "9", "u", direccion="Yatay 760", barrio="Almagro",
                                      precio=87000, moneda="USD", m2_totales=55), now)
        c, _ = repo.upsert(s, Listing("argenprop", "10", "u", direccion="Yatay 760", barrio="Boedo",
                                      precio=87000, moneda="USD", m2_totales=55), now)
        assert a.grupo_id == b.grupo_id == a.id and c.grupo_id is None


def test_desafio_anti_bot_por_titulo():
    from inmo.connectors.common import es_desafio
    assert es_desafio("<html><head><title>Just a moment...</title></head></html>")
    assert es_desafio("<title>Un momento…</title>")
    assert not es_desafio(HTML)                          # el listado real menciona "challenge" en scripts, no en el título


def test_parse_pictures_sin_estado_precargado_y_con_barras_escapadas():
    assert parse_pictures("<html></html>") == {}
    t = '"postingId":"1","url730x532":"https:\\/\\/img\\/a.jpg","url730x532":"https:\\/\\/img\\/a.jpg","postingId":"2","url730x532":"https://img/b.jpg"'
    assert parse_pictures(t) == {"1": ["https://img/a.jpg"], "2": ["https://img/b.jpg"]}
