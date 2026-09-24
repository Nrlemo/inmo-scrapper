"""Conectores Argenprop y MercadoLibre sobre páginas reales (fixtures recortadas: sin datos de la cuenta)."""
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import filtros, navegador
from inmo.connectors import REGISTRO
from inmo.connectors import argenprop as ap
from inmo.connectors import mercadolibre as ml
from inmo.models import Consulta, Publicacion, make_engine

FX = Path(__file__).parent / "fixtures"
HTML_AP = (FX / "argenprop_listado.html").read_text(encoding="utf-8")
HTML_ML = (FX / "mercadolibre_listado.html").read_text(encoding="utf-8")
T0 = datetime(2026, 9, 24, 3, 0)


# ---------- Argenprop ----------
def test_argenprop_parsea_la_pagina():
    ls, total = ap.parse_listing_page(HTML_AP)
    assert total == 433 and len(ls) == 20 and len({l.id_externo for l in ls}) == 20
    l = ls[0]
    assert (l.id_externo, l.url) == ("19746952", "https://www.argenprop.com/departamento-en-venta-en-villa-urquiza-3-ambientes--19746952")
    assert (l.precio, l.moneda, l.ambientes, l.dormitorios, l.banos, l.m2_cubiertos) == (125000, "USD", 3, 2, 2, 65)
    assert (l.direccion, l.barrio) == ("Mendoza 4900", "Villa Urquiza")
    assert (l.inmobiliaria_id_externo, l.inmobiliaria_nombre) == ("102597", "VAL-JOR INMOBILIARIA")
    assert len(l.fotos) == 8 and all(f.startswith("https://www.argenprop.com/static-content/") for f in l.fotos)
    assert l.titulo.startswith("Departamento 3 amb") and "Apto credito" in l.descripcion
    con_exp = [x for x in ls if x.expensas]
    assert len(con_exp) == 17 and con_exp[0].moneda_expensas == "ARS"
    assert all(x.precio and x.moneda == "USD" and x.barrio for x in ls)


def test_argenprop_paginacion_y_robots():
    u = "https://www.argenprop.com/departamentos/venta/palermo"
    assert ap.page_url(u, 1) == u and ap.page_url(u, 3) == u + "?pagina-3"
    assert ap.PORTAL.max_pages == 3                                            # robots.txt: ?pagina-1..3
    assert "dos o más «-o-»" in ap.problema_robots(
        "https://www.argenprop.com/casas-o-departamentos-o-ph/venta/capital-federal/dolares-hasta-125000")
    assert "consulta" in ap.problema_robots("https://www.argenprop.com/departamentos/venta/{zone}?solo-ver-dolares")
    assert ap.problema_robots("https://www.argenprop.com/departamentos/venta/{zone}/dolares-hasta-{price_max}") is None


# ---------- MercadoLibre ----------
def test_mercadolibre_parsea_la_pagina():
    ls, total = ml.parse_listing_page(HTML_ML)
    assert total == 34 and len(ls) == 34 and len({l.id_externo for l in ls}) == 34
    l = ls[0]
    assert l.id_externo == "MLA3987972974" and "#" not in l.url and "/MLA-3987972974-" in l.url
    assert (l.precio, l.moneda, l.ambientes, l.banos, l.m2_cubiertos) == (99000, "USD", 4, 1, 69)
    assert (l.direccion, l.barrio) == ("Peron Al 1200", "San Nicolás")
    assert all(x.id_externo.startswith("MLA") and x.precio and x.m2_cubiertos for x in ls)   # 7 sin el input oculto
    assert ml.PORTAL.miniatura(l.fotos[0]).endswith("_092026-V.webp") and "_2X_" not in ml.PORTAL.miniatura(l.fotos[0])


def test_mercadolibre_robots():
    u = "https://inmuebles.mercadolibre.com.ar/departamentos/venta/3-dormitorios/capital-federal/san-nicolas/"
    assert ml.problema_robots(u) is None
    assert "PriceRange" in ml.problema_robots(u + "_PriceRange_0USD-125000USD_NoIndex_True")
    assert "TOTAL*AREA" in ml.problema_robots(u + "_TOTAL*AREA_*-150m²")
    assert ml.page_url(u, 2).endswith("/san-nicolas/_Desde_49_NoIndex_True")


# ---------- en una ronda (registrados sólo en el test) ----------
@pytest.fixture
def con_portales(monkeypatch):
    monkeypatch.setitem(REGISTRO, "argenprop", ap.PORTAL)
    monkeypatch.setitem(REGISTRO, "mercadolibre", ml.PORTAL)


def test_ronda_por_argenprop_y_mercadolibre(tmp_path, con_portales):
    u_ap = "https://www.argenprop.com/departamentos/venta/capital-federal/dolares-hasta-125000/apto-credito"
    u_ml = "https://inmuebles.mercadolibre.com.ar/departamentos/venta/3-dormitorios/capital-federal/san-nicolas/"
    perfil = {"name": "t", "currency": "USD", "price": {"min": 1, "max": 125000}}
    cfg = {"profiles": [{**perfil, "portals": {"argenprop": {"search_urls": [u_ap]}}},
                        {**perfil, "portals": {"mercadolibre": {"search_urls": [u_ml]}}}], "politeness": {}}
    eng = make_engine(str(tmp_path / "t.sqlite"))
    with Session(eng) as s:
        r = navegador.iniciar(s, cfg, "ana", T0)
        assert r["url"] == u_ap and r["portal"] == "argenprop" and r["host"] == "https://www.argenprop.com/"
        rid = r["ronda"]
        r = navegador.pagina(s, cfg, rid, u_ap, HTML_AP, T0)
        assert r["siguiente"] == u_ap + "?pagina-2"                            # 433 resultados: 3 páginas (robots)
        otra = HTML_AP.replace('idaviso="', 'idaviso="9')                       # página 2 con otros ids
        r = navegador.pagina(s, cfg, rid, u_ap + "?pagina-2", otra, T0)
        assert r["siguiente"] == u_ap + "?pagina-3"
        r = navegador.pagina(s, cfg, rid, u_ap + "?pagina-3", HTML_AP.replace('idaviso="', 'idaviso="8'), T0)
        assert r["siguiente"] == u_ml and r["portal"] == "mercadolibre"
        r = navegador.pagina(s, cfg, rid, u_ml, HTML_ML, T0)                    # 34 resultados: una página
        assert r["fin"] and "433 resultados pero solo se pueden leer 60" in r["mensaje"]
        assert "Argenprop: 60 avisos" in r["mensaje"] and "MercadoLibre: 34 avisos" in r["mensaje"]
        por_portal = {p: len(s.scalars(select(Publicacion).where(Publicacion.portal == p)).all())
                      for p in ("argenprop", "mercadolibre")}
        assert por_portal == {"argenprop": 60, "mercadolibre": 34}
        assert {q.portal: q.completa for q in s.scalars(select(Consulta))} == {"argenprop": True, "mercadolibre": True}


def test_los_filtros_rechazan_urls_que_prohibe_robots(con_portales):
    doc = filtros.desde_yaml([{"name": "t", "currency": "USD", "price": {"min": 1, "max": 125000},
                               "portals": {"zonaprop": {"search_url_template": "https://www.zonaprop.com.ar/departamentos-venta-{zone}-{price_min}-{price_max}-dolar.html",
                                                        "zones": ["palermo"]}}}])
    b = doc["busquedas"][0]
    b["portales"]["argenprop"] = {"activo": True, "zonas": [{"zona": "palermo", "precio_min": None, "precio_max": None}],
                                  "ajustes": {}, "plantilla": "https://www.argenprop.com/casas-o-departamentos-o-ph/venta/{zone}"}
    with pytest.raises(ValueError, match="dos o más «-o-»"):
        filtros.validar(doc)
    b["portales"]["argenprop"]["plantilla"] = "https://www.argenprop.com/departamentos/venta/{zone}"
    b["portales"]["mercadolibre"] = {"activo": True, "zonas": [{"zona": "palermo", "precio_min": None, "precio_max": None}],
                                     "ajustes": {}, "plantilla": "https://inmuebles.mercadolibre.com.ar/departamentos/venta/capital-federal/{zone}/_PriceRange_0USD-{price_max}USD"}
    with pytest.raises(ValueError, match="PriceRange"):
        filtros.validar(doc)
    b["portales"]["mercadolibre"]["plantilla"] = "https://inmuebles.mercadolibre.com.ar/departamentos/venta/capital-federal/{zone}/"
    assert filtros.validar(doc)


def test_registrados_pero_apagados_hasta_configurarlos():
    """Entran en la ronda sólo si se prenden y tienen zonas (Estado → Búsqueda); con filtros guardados antes, quedan
    apagados."""
    assert list(REGISTRO)[:3] == ["zonaprop", "argenprop", "mercadolibre"]
    doc = filtros.desde_yaml([{"name": "t", "currency": "USD", "price": {"min": 1, "max": 2},
                               "portals": {"zonaprop": {"search_urls": ["https://www.zonaprop.com.ar/a.html"]}}}])
    pc = doc["busquedas"][0]["portales"]
    assert pc["zonaprop"]["activo"] and not pc["argenprop"]["activo"] and not pc["mercadolibre"]["activo"]
    assert [list(p["portals"]) for p in filtros.perfiles(doc)] == [["zonaprop"]]
    viejo = {"busquedas": [{"nombre": "t", "comunes": doc["busquedas"][0]["comunes"],
                            "portales": {"zonaprop": pc["zonaprop"]}}]}              # guardado cuando sólo existía Zonaprop
    nuevo = filtros.validar(viejo)["busquedas"][0]["portales"]
    assert not nuevo["argenprop"]["activo"] and not nuevo["mercadolibre"]["activo"]
