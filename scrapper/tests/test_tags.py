from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import tags
from inmo.connectors.base import Listing, SearchResult
from inmo.models import Categorizacion, make_engine
from inmo.runner import run

REGLAS = {
    "patio": ["patio"],
    "apto_credito": ["apto crédito", "apto credito", "apto banco"],
    "cochera": ["cochera", "garage"],
    "a_reciclar": "a reciclar",
}


def test_sin_tildes_ni_mayusculas_y_palabra_completa():
    r = tags.compilar(REGLAS)
    assert tags.etiquetar("Depto APTO CRÉDITO con Patio", r) == ["apto_credito", "patio"]
    assert tags.etiquetar("hermoso patio-jardín", r) == ["patio"]
    assert tags.etiquetar("patios internos, garages", r) == []          # palabra completa
    assert tags.etiquetar("PH a   reciclar.", r) == ["a_reciclar"]


def test_menciones_negadas_no_cuentan():
    r = tags.compilar(REGLAS)
    assert tags.etiquetar("Sin cochera. No tiene patio", r) == []
    assert tags.etiquetar("no apto crédito", r) == []
    assert tags.etiquetar("sin balcón ni patio, pero con cochera fija", r) == ["cochera"]


def test_reglas_mal_escritas():
    assert tags.compilar(None) == [] and tags.compilar({}) == []
    with pytest.raises(ValueError, match="auto_tags.patio"):
        tags.compilar({"patio": [1, 2]})
    with pytest.raises(ValueError):
        tags.compilar(["patio"])


class Fake:
    def __init__(self, listings):
        self.listings = listings

    def search(self, profile):
        return SearchResult(listings=self.listings, completa=True)


def _cfg(reglas):
    return {"profiles": [{"name": "t", "portals": {"zonaprop": {}}}], "politeness": {}, "auto_tags": reglas}


def test_runner_aplica_y_no_pisa_las_manuales(tmp_path):
    engine = make_engine(str(tmp_path / "t.sqlite"))
    ls = [Listing("zonaprop", "1", "u1", titulo="Con patio", descripcion="Apto crédito"),
          Listing("zonaprop", "2", "u2", titulo="Sin cochera")]
    run(engine, _cfg(REGLAS), "zonaprop", now=datetime(2026, 9, 1), connector=Fake(ls), skip_gap=True)
    with Session(engine) as s:
        c1, c2 = s.scalars(select(Categorizacion).order_by(Categorizacion.publicacion_id)).all()
        assert c1.etiquetas_auto == ["apto_credito", "patio"] and c2.etiquetas_auto == []
        c1.etiquetas = ["ver"]                                               # etiqueta manual
        s.commit()
    # Cambian las reglas: se recalcula todo lo guardado; las manuales quedan intactas.
    run(engine, _cfg({"credito": ["apto crédito"]}), "zonaprop", now=datetime(2026, 9, 2), connector=Fake(ls),
        skip_gap=True)
    with Session(engine) as s:
        c1 = s.get(Categorizacion, 1)
        assert c1.etiquetas_auto == ["credito"] and c1.etiquetas == ["ver"]


def test_reglas_invalidas_no_rompen_la_corrida(tmp_path):
    engine = make_engine(str(tmp_path / "t.sqlite"))
    out = run(engine, _cfg({"patio": 5}), "zonaprop", now=datetime(2026, 9, 1),
              connector=Fake([Listing("zonaprop", "1", "u1", titulo="Con patio")]), skip_gap=True)
    assert out["t"]["nuevas"] == 1
