"""Ronda por navegador con dos portales: Zonaprop (real) y un portal falso registrado sólo en estos tests."""
import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import navegador
from inmo.connectors import REGISTRO
from inmo.connectors.base import Listing, Portal
from inmo.models import Consulta, Ejecucion, Publicacion, RondaNavegador, make_engine

HTML_ZP = (Path(__file__).parent / "fixtures" / "zonaprop_listado.html").read_text(encoding="utf-8")
CHICA_ZP = HTML_ZP.replace("2.864", "25").replace("2864", "25")        # una sola página
T0 = datetime(2026, 9, 24, 3, 0)
URL_ZP = "https://www.zonaprop.com.ar/a.html"
URL_F = "https://falso.test/busqueda"


class Falso(Portal):
    """Portal mínimo: <title>N resultados</title> y un <li data-id data-precio> por aviso."""
    nombre, etiqueta, host, page_size, max_pages = "falso", "Falso", "https://falso.test/", 2, 3

    def page_url(self, url, n):
        return url if n <= 1 else f"{url}?p={n}"

    def parse_page(self, html):
        m = re.search(r"<title>(\d+) resultados</title>", html)
        ls = [Listing("falso", i, f"https://falso.test/{i}", precio=float(p), moneda="USD", titulo=f"Falso {i}")
              for i, p in re.findall(r'<li data-id="(\w+)" data-precio="(\d+)">', html)]
        return ls, int(m.group(1)) if m else None


def pagina_falsa(total, *ids, precio=100000):
    return f"<html><title>{total} resultados</title>" + "".join(f'<li data-id="{i}" data-precio="{precio}">' for i in ids)


@pytest.fixture(autouse=True)
def falso(monkeypatch):
    monkeypatch.setitem(REGISTRO, "falso", Falso())


def _cfg(**pol_falso):
    perfil = {"name": "t", "currency": "USD", "price": {"min": 1, "max": 10**7}}
    return {"profiles": [{**perfil, "portals": {"zonaprop": {"search_urls": [URL_ZP]}}},
                         {**perfil, "portals": {"falso": {"search_urls": [URL_F]}}}],
            "politeness": {"zonaprop": {"page_delay": [20, 45], "zone_delay": [60, 150]},
                           "falso": {"page_delay": [90, 100], "zone_delay": [200, 210], **pol_falso}}}


def test_una_ronda_recorre_los_dos_portales(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        r = navegador.iniciar(s, cfg, "ana", T0)
        assert r["url"] == URL_ZP and r["portal"] == "zonaprop" and r["host"] == "https://www.zonaprop.com.ar/"
        rid = r["ronda"]
        assert s.get(Ejecucion, rid).portal == "zonaprop,falso" and s.get(Ejecucion, rid).zona_actual.startswith("Zonaprop · ")
        r = navegador.pagina(s, cfg, rid, URL_ZP, CHICA_ZP, T0)
        assert r == {"siguiente": URL_F, "pausa": r["pausa"], "portal": "falso", "host": "https://falso.test/"}
        assert 200 <= r["pausa"] <= 210                                    # cadencia del portal falso
        r = navegador.pagina(s, cfg, rid, URL_F, pagina_falsa(3, "f1", "f2"), T0)
        assert r["siguiente"] == URL_F + "?p=2" and 90 <= r["pausa"] <= 100 and r["portal"] == "falso"
        r = navegador.pagina(s, cfg, rid, URL_F + "?p=2", pagina_falsa(3, "f3"), T0)
        assert r["fin"] and r["estado"] == "ok"
        assert "Zonaprop:" in r["mensaje"] and "Falso: 3 avisos" in r["mensaje"]
        consultas = {(q.portal, q.perfil): q for q in s.scalars(select(Consulta))}
        assert set(consultas) == {("zonaprop", "t"), ("falso", "t")} and all(q.completa for q in consultas.values())
        assert {p.id_externo for p in s.scalars(select(Publicacion).where(Publicacion.portal == "falso"))} == {"f1", "f2", "f3"}


def test_un_bloqueo_no_corta_al_otro_portal_y_el_cooldown_es_por_portal(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        r = navegador.pagina(s, cfg, rid, URL_ZP, "<html><title>Just a moment...</title></html>", T0)
        assert r["siguiente"] == URL_F and r["portal"] == "falso"          # sigue con el otro portal
        r = navegador.pagina(s, cfg, rid, URL_F, pagina_falsa(1, "f1"), T0)
        assert r["fin"] and r["estado"] == "parcial" and "desafío anti-bot" in r["mensaje"]
        q = {x.portal: x for x in s.scalars(select(Consulta))}
        assert q["zonaprop"].bloqueada and not q["zonaprop"].completa and q["falso"].completa and not q["falso"].bloqueada
        # Al día siguiente: Zonaprop sigue en cooldown (24 h), el falso corre solo
        r = navegador.iniciar(s, cfg, "ana", T0 + timedelta(hours=21))
        assert r["portal"] == "falso" and r["url"] == URL_F
        assert s.get(Ejecucion, r["ronda"]).portal == "falso"
        r = navegador.pagina(s, cfg, r["ronda"], URL_F, pagina_falsa(1, "f1"), T0 + timedelta(hours=21))
        assert "Omitido — Zonaprop: cooldown por bloqueo" in r["mensaje"]


def test_si_bloquean_todos_la_ronda_queda_bloqueada(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        navegador.pagina(s, cfg, rid, URL_ZP, "<title>Un momento…</title>", T0)
        r = navegador.pagina(s, cfg, rid, URL_F, "<title>Just a moment...</title>", T0)
        assert r["fin"] and r["estado"] == "bloqueada"
        assert "cooldown" in navegador.iniciar(s, cfg, "ana", T0 + timedelta(hours=2), forzar=True)["omitir"]


def test_bajas_por_portal(tmp_path):
    """El portal falso completa sus rondas y da de baja lo que ya no aparece; los avisos de Zonaprop (bloqueado)
    no se tocan."""
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg(min_hours_between_runs=0)
    cfg["politeness"]["zonaprop"]["min_hours_between_runs"] = 0
    cfg["politeness"]["zonaprop"]["cooldown_hours_on_block"] = 0
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        navegador.pagina(s, cfg, rid, URL_ZP, CHICA_ZP, T0)
        navegador.pagina(s, cfg, rid, URL_F, pagina_falsa(2, "viejo", "f1"), T0)
        n_zp = len(s.scalars(select(Publicacion).where(Publicacion.portal == "zonaprop")).all())
        for k in range(1, 4):                                               # 3 rondas sin «viejo», Zonaprop bloqueado
            t = T0 + timedelta(days=k)
            rid = navegador.iniciar(s, cfg, "ana", t)["ronda"]
            navegador.pagina(s, cfg, rid, URL_ZP, "<title>Just a moment...</title>", t)
            navegador.pagina(s, cfg, rid, URL_F, pagina_falsa(1, "f1"), t)
        pubs = {(p.portal, p.id_externo): p for p in s.scalars(select(Publicacion))}
        assert pubs[("falso", "viejo")].activa is False and pubs[("falso", "f1")].activa is True
        assert sum(p.activa for (portal, _), p in pubs.items() if portal == "zonaprop") == n_zp


def test_intervalo_minimo_por_portal(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg(min_hours_between_runs=48)
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        navegador.pagina(s, cfg, rid, URL_ZP, CHICA_ZP, T0)
        navegador.pagina(s, cfg, rid, URL_F, pagina_falsa(1, "f1"), T0)
        r = navegador.iniciar(s, cfg, "ana", T0 + timedelta(hours=21))    # Zonaprop (20 h) sí, el falso (48 h) no
        assert r["portal"] == "zonaprop" and s.get(Ejecucion, r["ronda"]).portal == "zonaprop"
        r = navegador.pagina(s, cfg, r["ronda"], URL_ZP, CHICA_ZP, T0 + timedelta(hours=21))
        assert r["fin"] and "Omitido — Falso: última consulta" in r["mensaje"]


def test_ronda_guardada_con_el_formato_anterior_se_retoma(tmp_path):
    """Una ronda en curso cuando se actualiza el servidor: estado sin portales (sólo Zonaprop)."""
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        e = Ejecucion(portal="zonaprop", zonas=[], usuario="ana (navegador)", inicio=T0, actualizado=T0,
                      estado="corriendo", zonas_total=1, zona_idx=1, zona_actual="a")
        s.add(e)
        s.flush()
        s.add(RondaNavegador(id=e.id, datos={
            "zonas": [{"perfil": "t", "zona": "a", "url": URL_ZP}], "idx": 0, "pagina": 1, "paginas": None,
            "zona_vistos": [], "vistos": {"t": []}, "stats": {"t": {"nuevas": 0, "precio_cambiado": 0, "vistas": 0}},
            "completa": True, "truncada": False, "errores": []}))
        s.commit()
        r = navegador.pagina(s, cfg, e.id, URL_ZP, CHICA_ZP, T0)
        assert r["fin"] and r["estado"] == "ok" and s.scalars(select(Consulta)).one().portal == "zonaprop"


def test_fuera_de_connectors_nadie_nombra_un_portal():
    """Criterio de #20: la ronda, los filtros y la web pasan por el registro (connectors/__init__.py)."""
    raiz = Path(__file__).resolve().parents[2]
    archivos = [p for p in (raiz / "scrapper" / "src" / "inmo").rglob("*.py") if "connectors" not in p.parts]
    archivos += list((raiz / "web" / "app").rglob("*.py"))
    nombres = re.compile(r"\b(zonaprop|argenprop|mercadolibre)\b", re.I)   # palabra completa: «mercadolibre1» (clave trivial) no cuenta
    for f in archivos:
        arbol = ast.parse(f.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(arbol)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(arbol):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
                assert not nombres.search(n.value), f"{f.name}:{n.lineno} {n.value!r}"
