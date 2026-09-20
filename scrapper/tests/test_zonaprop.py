from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo.connectors.base import Listing
from inmo.connectors.zonaprop import (ZonapropConnector, _money, _num, matches_profile, page_url,
                                      parse_listing_page)
from inmo.errors import BlockedError
from inmo.models import Consulta, HistorialPrecio, Publicacion, make_engine
from inmo import repo
from inmo.runner import run

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


class FakeClient:
    """Sirve el fixture como página 1 y páginas sucesivas con avisos distintos; registra los pedidos."""
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url, delay=None):
        self.urls.append(url)
        p = self.pages[len(self.urls) - 1]
        if isinstance(p, Exception):
            raise p
        return p


def _profile():
    return {"name": "t", "currency": "USD", "price": {"min": 1, "max": 10**7}, "portals": {"zonaprop": {
        "search_url": "https://www.zonaprop.com.ar/x.html"}}}


def _cfg(max_pages=5):
    return {"page_delay": [0, 0], "max_pages": max_pages}


def test_pagination_stops_on_repeated_page():
    fake = FakeClient([HTML, HTML])                        # página 2 repite ids => corta
    res = ZonapropConnector(_cfg(), fake).search(_profile())
    assert len(fake.urls) == 2 and fake.urls[1].endswith("x-pagina-2.html")
    assert res.completa and not res.bloqueada
    assert res.truncada and any("solo se pueden leer" in e for e in res.errores)  # 2864 > 150


def test_page_cap_from_config():
    other = HTML.replace('data-id="', 'data-id="9')       # ids distintos => no corta por repetición
    fake = FakeClient([HTML, other, other.replace('data-id="9', 'data-id="8')])
    ZonapropConnector(_cfg(max_pages=3), fake).search(_profile())
    assert len(fake.urls) == 3


def test_block_is_reported_not_raised():
    res = ZonapropConnector(_cfg(), FakeClient([HTML, BlockedError("HTTP 403")])).search(_profile())
    assert res.bloqueada and not res.completa and len(res.listings) > 0


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


def test_runner_cooldown_after_block(engine):
    class Blocked:
        def search(self, profile):
            from inmo.connectors.base import SearchResult
            return SearchResult(completa=False, bloqueada=True, errores=["HTTP 403"])
    cfg = {"profiles": [_profile()], "politeness": {"zonaprop": {"cooldown_hours_on_block": 24}}}
    t0 = datetime(2026, 9, 20, 10)
    out = run(engine, cfg, "zonaprop", now=t0, connector=Blocked())
    assert out["t"]["bloqueada"]
    out = run(engine, cfg, "zonaprop", now=t0 + timedelta(hours=5), connector=Blocked())
    assert "cooldown" in out["t"]
    with Session(engine) as s:
        assert len(s.scalars(select(Consulta)).all()) == 1


def test_multi_zone_urls_dedupe_and_truncation():
    from inmo.connectors.zonaprop import search_urls
    prof = _profile()
    prof["portals"]["zonaprop"] = {"search_url_template": "https://www.zonaprop.com.ar/d-{zone}-x.html",
                                   "zones": ["almagro", "boedo"]}
    assert [z for z, _ in search_urls(prof)] == ["almagro", "boedo"]
    small = HTML.replace("2.864 Departamentos", "40 Departamentos")   # 2 páginas por zona
    fake = FakeClient([small, "<html><title>x</title></html>", small, "<html><title>x</title></html>"])
    res = ZonapropConnector({"page_delay": [0, 0], "max_pages": 5}, fake).search(prof)
    assert fake.urls[0].endswith("d-almagro-x.html") and fake.urls[2].endswith("d-boedo-x.html")
    ids = [l.id_externo for l in res.listings]
    assert len(ids) == len(set(ids)) and not res.truncada and res.completa   # boedo repite ids => no duplica


def test_block_in_one_zone_stops_the_rest():
    prof = _profile()
    prof["portals"]["zonaprop"] = {"search_url_template": "https://x/{zone}.html", "zones": ["a", "b"]}
    fake = FakeClient([BlockedError("HTTP 403"), HTML])
    res = ZonapropConnector({"page_delay": [0, 0]}, fake).search(prof)
    assert res.bloqueada and len(fake.urls) == 1 and "[a]" in res.errores[0]


def test_truncated_search_does_not_inactivate(engine):
    class Trunc:
        def search(self, profile):
            from inmo.connectors.base import SearchResult
            return SearchResult(truncada=True)
    cfg = {"profiles": [_profile()], "politeness": {"zonaprop": {}}}
    t0 = datetime(2026, 9, 20)
    with Session(engine) as s:
        p, _ = repo.upsert(s, Listing("zonaprop", "1", "u", precio=1, moneda="USD"), t0)
        s.commit()
    for d in range(1, 5):
        run(engine, cfg, "zonaprop", now=t0 + timedelta(days=d), connector=Trunc())
    with Session(engine) as s:
        assert s.scalar(select(Publicacion)).activa is True


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


def test_tracker_records_progress_and_skip_gap(engine):
    from inmo.models import Ejecucion
    from inmo.progress import Tracker
    now = datetime(2026, 1, 1, 12)
    with Session(engine) as s:
        s.add(Ejecucion(id=1, portal="zonaprop", zonas=[], inicio=now, actualizado=now, estado="corriendo"))
        s.commit()
    t = Tracker(engine, 1)
    t.update(zonas_total=3, zona_idx=2, zona_actual="almagro", pagina=1, paginas=4)
    with Session(engine) as s:
        e = s.get(Ejecucion, 1)
        assert (e.zonas_total, e.zona_idx, e.zona_actual, e.pagina, e.paginas) == (3, 2, "almagro", 1, 4)
    t.finish("ok", "listo")
    t.update(zona_idx=3)                     # ya finalizada: no se pisa
    with Session(engine) as s:
        e = s.get(Ejecucion, 1)
        assert e.estado == "ok" and e.zona_idx == 2 and e.fin is not None


def test_user_agent_has_no_contact_unless_configured(monkeypatch):
    import importlib
    import inmo.http as h
    monkeypatch.delenv("INMO_CONTACT", raising=False)
    assert "contacto" not in importlib.reload(h).UA
    monkeypatch.setenv("INMO_CONTACT", "yo@example.com")
    assert "contacto: yo@example.com" in importlib.reload(h).UA
    monkeypatch.delenv("INMO_CONTACT")
    importlib.reload(h)


def test_client_uses_http2_and_falls_back_without_h2(monkeypatch, caplog):
    import httpx
    from inmo import http as h
    c = h._nuevo_cliente(5)
    assert c._transport._pool._http2                      # HTTP/2 habilitado (h2 instalado)
    c.close()
    real = httpx.Client

    def sin_h2(*a, http2=False, **kw):
        if http2:
            raise ImportError("h2")
        return real(*a, **kw)
    monkeypatch.setattr(h.httpx, "Client", sin_h2)
    with caplog.at_level("WARNING"):
        c = h._nuevo_cliente(5)
    assert not c._transport._pool._http2 and "HTTP/1.1" in caplog.text     # sin h2: avisa y sigue
    c.close()
