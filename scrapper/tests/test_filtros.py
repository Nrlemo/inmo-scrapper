from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from inmo import filtros, repo
from inmo.connectors.base import Listing
from inmo.connectors.zonaprop import search_urls
from inmo.models import Publicacion, make_engine

TPL = ("https://www.zonaprop.com.ar/departamentos-venta-{zone}-con-apto-credito-mas-de-2-habitaciones-"
       "mas-de-3-ambientes-{price_min}-{price_max}-dolar.html")
YAML = [{"name": "caba-apto-credito", "operation": "compra", "type": "departamento", "currency": "USD",
         "price": {"min": 50000, "max": 125000}, "rooms": {"min": 3}, "total_m2_min": 60,
         "exclude_keywords": ["pozo", "sin escritura"],
         "portals": {"zonaprop": {"search_url_template": TPL,
                                  "zones": [{"zone": "almagro", "price_max": 110000}, {"zone": "almagro", "price_min": 110000},
                                            "boedo"]}}}]


def test_importa_el_yaml_y_genera_las_mismas_urls():
    doc = filtros.desde_yaml(YAML)
    b = doc["busquedas"][0]
    assert b["comunes"]["apto_credito"] and b["comunes"]["dorm_min"] == 2 and b["comunes"]["amb_min"] == 3
    zp = b["portales"]["zonaprop"]
    assert zp["activo"] and zp["plantilla"] is None and not b["portales"]["argenprop"]["activo"]
    antes = search_urls(YAML[0])
    despues = search_urls(filtros.perfiles(doc)[0])
    assert despues == antes and len(despues) == 3                    # la ronda recorre exactamente lo mismo


def test_plantilla_desconocida_queda_como_avanzada():
    y = [{**YAML[0], "portals": {"zonaprop": {"search_url_template": "https://www.zonaprop.com.ar/ph-venta-{zone}.html",
                                              "zones": ["boedo"]}}}]
    doc = filtros.desde_yaml(y)
    assert doc["busquedas"][0]["portales"]["zonaprop"]["plantilla"].endswith("ph-venta-{zone}.html")
    assert search_urls(filtros.perfiles(doc)[0]) == [("boedo", "https://www.zonaprop.com.ar/ph-venta-boedo.html")]


def test_ajustes_por_portal_pisan_los_comunes():
    doc = filtros.desde_yaml(YAML)
    doc["busquedas"][0]["portales"]["zonaprop"]["ajustes"] = {"precio_max": "100.000", "m2_tot_min": ""}
    p = filtros.perfiles(filtros.validar(doc))[0]
    assert p["price"] == {"min": 50000, "max": 100000} and p["total_m2_min"] == 60     # vacío = hereda
    assert search_urls(p)[-1][1].endswith("-50000-100000-dolar.html")


def test_zonas_como_texto_ida_y_vuelta():
    z = filtros.zonas_desde_texto("Almagro -110000\n\nalmagro 110000-\nvilla-crespo\n")
    assert z == [{"zona": "almagro", "precio_min": None, "precio_max": 110000},
                 {"zona": "almagro", "precio_min": 110000, "precio_max": None},
                 {"zona": "villa-crespo", "precio_min": None, "precio_max": None}]
    assert filtros.zonas_a_texto(z) == "almagro -110000\nalmagro 110000-\nvilla-crespo"
    with pytest.raises(ValueError, match="línea 2"):
        filtros.zonas_desde_texto("boedo\nvilla crespo")


@pytest.mark.parametrize("cambio, error", [
    (lambda b: b["comunes"].update(precio_min=200000), "mínimo es mayor"),
    (lambda b: b["comunes"].update(precio_max=None), "precio mínimo y máximo"),
    (lambda b: b["portales"]["zonaprop"].update(zonas=[]), "no tiene zonas"),
    (lambda b: b["comunes"].update(tipo="casa"), "no está verificado"),
    (lambda b: b["portales"]["zonaprop"].update(plantilla="https://x/sin-zona.html"), "{zone}"),
    (lambda b: b["comunes"].update(m2_tot_min="muchos"), "no es un número"),
])
def test_validacion(cambio, error):
    doc = filtros.desde_yaml(YAML)
    cambio(doc["busquedas"][0])
    with pytest.raises(ValueError, match=error):
        filtros.validar(doc)


def test_casa_con_plantilla_avanzada_es_valida():
    doc = filtros.desde_yaml(YAML)
    b = doc["busquedas"][0]
    b["comunes"]["tipo"] = "casa"
    b["portales"]["zonaprop"]["plantilla"] = "https://www.zonaprop.com.ar/casas-venta-{zone}.html"
    assert filtros.validar(doc)


def test_recalcular_oculta_y_devuelve(tmp_path):
    eng = make_engine(str(tmp_path / "t.sqlite"))
    now = datetime(2026, 9, 24)
    with Session(eng) as s:
        repo.upsert(s, Listing("zonaprop", "1", "u", precio=90000, moneda="USD", m2_totales=62), now)
        repo.upsert(s, Listing("zonaprop", "2", "u", precio=90000, moneda="USD", m2_totales=None), now)   # sin dato: no se descarta
        repo.upsert(s, Listing("argenprop", "3", "u", precio=1, moneda="USD"), now)                      # portal sin perfil: no cambia
        doc = filtros.desde_yaml(YAML)
        doc["busquedas"][0]["comunes"]["m2_tot_min"] = 65
        assert filtros.recalcular(s, filtros.perfiles(filtros.validar(doc))) == 1
        assert [p.fuera_filtro for p in s.query(Publicacion).order_by(Publicacion.id)] == [True, False, False]
        doc["busquedas"][0]["comunes"]["m2_tot_min"] = 60                                               # se afloja: vuelve
        assert filtros.recalcular(s, filtros.perfiles(filtros.validar(doc))) == 0
        s.get(Publicacion, 1).fuera_filtro = True
        repo.upsert(s, Listing("zonaprop", "1", "u", precio=90000, moneda="USD", m2_totales=62), now)  # la ronda lo vio y cumple
        assert s.get(Publicacion, 1).fuera_filtro is False


def test_dormitorios_minimos_se_filtran_al_recibir():
    from inmo.connectors.common import matches_profile
    p = filtros.perfiles(filtros.desde_yaml(YAML))[0]
    assert not matches_profile(Listing("zonaprop", "1", "u", dormitorios=1), p)
    assert matches_profile(Listing("zonaprop", "1", "u", dormitorios=2), p)


def test_maximos_de_ambientes_y_dormitorios_globales_y_por_portal():
    from inmo.connectors.common import matches_profile
    doc = filtros.desde_yaml(YAML)
    b = doc["busquedas"][0]
    b["comunes"].update(amb_max=4, dorm_max=3)
    p = filtros.perfiles(filtros.validar(doc))[0]
    assert p["rooms"] == {"min": 3, "max": 4} and p["bedrooms"] == {"min": 2, "max": 3}
    assert not matches_profile(Listing("zonaprop", "1", "u", ambientes=5), p)
    assert not matches_profile(Listing("zonaprop", "1", "u", dormitorios=4), p)
    assert matches_profile(Listing("zonaprop", "1", "u", ambientes=4, dormitorios=3), p)
    b["portales"]["zonaprop"]["ajustes"] = {"amb_max": "3", "dorm_max": "2"}          # Zonaprop pisa los globales
    p = filtros.perfiles(filtros.validar(doc))[0]
    assert p["rooms"]["max"] == 3 and p["bedrooms"]["max"] == 2
    assert not matches_profile(Listing("zonaprop", "1", "u", ambientes=4), p)
    assert search_urls(p) == search_urls(YAML[0])                                     # los máximos no van en la URL


def test_dormitorios_minimo_mayor_que_maximo():
    doc = filtros.desde_yaml(YAML)
    doc["busquedas"][0]["comunes"].update(dorm_min=3, dorm_max=2)
    with pytest.raises(ValueError, match="dormitorios mínimo es mayor"):
        filtros.validar(doc)
    doc = filtros.desde_yaml(YAML)
    doc["busquedas"][0]["portales"]["zonaprop"]["ajustes"] = {"dorm_max": 1}         # 1 < dorm_min 2 común
    with pytest.raises(ValueError, match="en Zonaprop"):
        filtros.validar(doc)


def test_filtros_guardados_antes_de_los_maximos_siguen_andando():
    doc = filtros.desde_yaml(YAML)
    del doc["busquedas"][0]["comunes"]["dorm_max"]                                    # como están en producción
    p = filtros.perfiles(doc)[0]
    assert p["bedrooms"] == {"min": 2, "max": None}
