from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import navegador
from inmo.models import Consulta, Ejecucion, Publicacion, make_engine

HTML = (Path(__file__).parent / "fixtures" / "zonaprop_listado.html").read_text(encoding="utf-8")
T0 = datetime(2026, 9, 24, 3, 0)
URL1 = "https://www.zonaprop.com.ar/a.html"
URL2 = "https://www.zonaprop.com.ar/b.html"


def _cfg(urls=(URL1,), **pol):
    return {"profiles": [{"name": "t", "currency": "USD", "price": {"min": 1, "max": 10**7},
                          "portals": {"zonaprop": {"search_urls": list(urls)}}}],
            "politeness": {"zonaprop": {"page_delay": [20, 45], "zone_delay": [60, 150], "max_pages": 3, **pol}}}


def _chica(html=HTML):
    """Misma página, pero con 25 resultados en total: una sola página, sin truncar."""
    return html.replace("2.864", "25").replace("2864", "25")


def _otra(html, prefijo):
    """La misma página con otros ids (para simular la página siguiente)."""
    return html.replace('data-id="', f'data-id="{prefijo}').replace('"postingId":"', f'"postingId":"{prefijo}')


def test_ronda_completa_pagina_a_pagina(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg((URL1, URL2))
    with Session(eng) as s:
        r = navegador.iniciar(s, cfg, "ana", T0)
        assert r["url"] == URL1 and r["ronda"]
        rid = r["ronda"]
        # zona 1: 2864 resultados -> truncada, 3 páginas (max_pages)
        r = navegador.pagina(s, cfg, rid, URL1, HTML, T0)
        assert r["siguiente"] == "https://www.zonaprop.com.ar/a-pagina-2.html" and 30 <= r["pausa"] <= 45
        e = s.get(Ejecucion, rid)
        assert e.estado == "corriendo" and e.paginas == 3 and e.resultados > 20 and e.usuario == "ana (navegador)"
        r = navegador.pagina(s, cfg, rid, r["siguiente"], _otra(HTML, "9"), T0)
        assert r["siguiente"].endswith("a-pagina-3.html")
        r = navegador.pagina(s, cfg, rid, r["siguiente"], _otra(HTML, "9"), T0)   # repite ids: corta la zona
        assert r["siguiente"] == URL2 and r["pausa"] >= 60
        r = navegador.pagina(s, cfg, rid, URL2, _otra(HTML, "8"), T0)
        r = navegador.pagina(s, cfg, rid, r["siguiente"], _otra(HTML, "8"), T0)
        assert r["fin"] and r["estado"] == "ok"
        assert "avisos" in r["mensaje"] and "solo se pueden leer" in r["mensaje"]
        pubs = s.scalars(select(Publicacion)).all()
        assert len(pubs) > 60 and any(len(p.fotos) == 8 for p in pubs)
        q = s.scalars(select(Consulta)).one()
        assert q.completa and not q.bloqueada and q.cantidad_resultados == len(pubs)


def test_bajas_solo_tras_ronda_completa_y_no_truncada(tmp_path):
    eng = make_engine(str(tmp_path / "t.sqlite"))
    cfg = _cfg(min_hours_between_runs=0)
    chica = _chica()
    with Session(eng) as s:
        for k in range(4):                              # visto en la 1ª; 3 rondas sin verlo -> baja
            rid = navegador.iniciar(s, cfg, "ana", T0 + timedelta(days=k))["ronda"]
            if k == 0:
                navegador.pagina(s, cfg, rid, URL1, chica, T0)
                s.get(Publicacion, 1).id_externo = "ya-no-esta"
                s.commit()
            else:
                navegador.pagina(s, cfg, rid, URL1, chica, T0 + timedelta(days=k))
        p = s.scalars(select(Publicacion).where(Publicacion.id_externo == "ya-no-esta")).one()
        assert p.activa is False and p.consultas_sin_ver == 3


def test_desafio_anti_bot_bloquea_y_da_cooldown(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        r = navegador.pagina(s, cfg, rid, URL1, "<html><title>Un momento…</title></html>", T0)
        assert r["fin"] and r["estado"] == "bloqueada"
        assert s.scalars(select(Consulta)).one().bloqueada
        assert "cooldown" in navegador.iniciar(s, cfg, "ana", T0 + timedelta(hours=2))["omitir"]


def test_omitir_por_intervalo_minimo_y_por_corrida_en_curso(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        assert "en curso" in navegador.iniciar(s, cfg, "beto", T0)["omitir"]
        navegador.pagina(s, cfg, rid, URL1, _chica(), T0)
        assert "mín." in navegador.iniciar(s, cfg, "ana", T0 + timedelta(hours=5))["omitir"]
        assert "ronda" in navegador.iniciar(s, cfg, "ana", T0 + timedelta(hours=21))


def test_error_de_carga_pagina_desfasada_y_cancelada(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg((URL1, URL2))
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        assert navegador.pagina(s, cfg, rid, URL2, HTML, T0) == {"siguiente": URL1, "pausa": 30}   # no era la esperada
        r = navegador.error(s, cfg, rid, URL1, "tiempo de espera agotado", T0)
        assert r["siguiente"] == URL2
        s.get(Ejecucion, rid).estado = "cancelada"
        s.commit()
        assert navegador.pagina(s, cfg, rid, URL2, HTML, T0)["fin"]
        assert s.scalars(select(Consulta)).all() == []            # cancelada: no se registra ni da de baja


def test_ronda_abandonada_se_interrumpe_y_cancelar(tmp_path):
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg()
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
        assert "en curso" in navegador.iniciar(s, cfg, "ana", T0 + timedelta(minutes=10))["omitir"]
        nueva = navegador.iniciar(s, cfg, "ana", T0 + timedelta(minutes=40))      # la anterior quedó abandonada
        assert s.get(Ejecucion, rid).estado == "interrumpida" and nueva["ronda"] != rid
        assert navegador.cancelar(s, nueva["ronda"])["estado"] == "cancelada"
        assert navegador.pagina(s, cfg, nueva["ronda"], URL1, HTML, T0)["fin"]


def test_estado_persiste_entre_sesiones(tmp_path):
    """En el servidor cada página llega en un pedido (y una sesión) distinto."""
    eng, cfg = make_engine(str(tmp_path / "t.sqlite")), _cfg((URL1, URL2))
    with Session(eng) as s:
        rid = navegador.iniciar(s, cfg, "ana", T0)["ronda"]
    with Session(eng) as s:
        sig = navegador.pagina(s, cfg, rid, URL1, HTML, T0)["siguiente"]
    with Session(eng) as s:
        r = navegador.pagina(s, cfg, rid, sig, _otra(HTML, "9"), T0)
        assert r["siguiente"].endswith("a-pagina-3.html")              # aceptó la página 2: el estado se guardó
    with Session(eng) as s:
        r = navegador.error(s, cfg, rid, r["siguiente"], "timeout", T0)
        assert r["siguiente"] == URL2
    with Session(eng) as s:
        assert navegador.pagina(s, cfg, rid, URL2, _chica(), T0)["estado"] == "parcial"
        assert "timeout" in s.get(Ejecucion, rid).mensaje
