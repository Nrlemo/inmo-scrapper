from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import navegador, repo, tags
from inmo.connectors.base import Listing
from inmo.models import Categorizacion, make_engine

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


def test_aplica_a_lo_guardado_y_no_pisa_las_manuales(tmp_path):
    engine = make_engine(str(tmp_path / "t.sqlite"))
    with Session(engine) as s:
        repo.upsert(s, Listing("zonaprop", "1", "u1", titulo="Con patio", descripcion="Apto crédito"), datetime(2026, 9, 1))
        repo.upsert(s, Listing("zonaprop", "2", "u2", titulo="Sin cochera"), datetime(2026, 9, 1))
        assert tags.aplicar(s, REGLAS) == 1
        s.commit()
        c1, c2 = s.scalars(select(Categorizacion).order_by(Categorizacion.publicacion_id)).all()
        assert c1.etiquetas_auto == ["apto_credito", "patio"] and c2.etiquetas_auto == []
        c1.etiquetas = ["ver"]                                               # etiqueta manual
        s.commit()
        # Cambian las reglas: se recalcula todo lo guardado; las manuales quedan intactas.
        tags.aplicar(s, {"credito": ["apto crédito"]})
        s.commit()
        c1 = s.get(Categorizacion, 1)
        assert c1.etiquetas_auto == ["credito"] and c1.etiquetas == ["ver"]


def test_reglas_invalidas_no_rompen_la_ronda(tmp_path):
    html = (Path(__file__).parent / "fixtures" / "zonaprop_listado.html").read_text(encoding="utf-8")
    html = html.replace("2.864", "25").replace("2864", "25")                # una sola página
    url = "https://www.zonaprop.com.ar/a.html"
    cfg = {"profiles": [{"name": "t", "portals": {"zonaprop": {"search_urls": [url]}}}],
           "politeness": {}, "auto_tags": {"patio": 5}}
    engine = make_engine(str(tmp_path / "t.sqlite"))
    with Session(engine) as s:
        r = navegador.iniciar(s, cfg, "ana", datetime(2026, 9, 1))
        r = navegador.pagina(s, cfg, r["ronda"], url, html, datetime(2026, 9, 1))
        assert r["fin"] and r["estado"] == "ok" and "0 nuevos" not in r["mensaje"]
