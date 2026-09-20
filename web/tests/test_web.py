HX = {"HX-Request": "true"}


def test_requires_identity(client):
    r = client.get("/", headers={"X-authentik-username": ""})
    assert r.status_code == 401


def test_post_requires_htmx_header(client):
    assert client.post("/p/1/accion", data={"accion": "favorito"}).status_code == 403


def test_all_pages_render(client):
    for url in ["/", "/lista", "/cambios", "/comparar", "/mapa", "/estado", "/p/1", "/lista?q=Calle&baja=1&nuevas=1&orden=usd_m2"]:
        r = client.get(url)
        assert r.status_code == 200, url
    assert "&lt;b&gt;" in client.get("/p/1").text          # escapado de contenido scrapeado


def test_review_flow_and_discard(client):
    assert "3 sin revisar" in client.get("/").text
    r = client.post("/p/3/accion", data={"accion": "descartar", "vista": "card"}, headers=HX)
    assert r.status_code == 200
    assert "2 sin revisar" in client.get("/").text
    assert "3 resultados" not in client.get("/lista?estado=activas").text
    assert "1 resultados" in client.get("/lista?estado=descartadas").text
    client.post("/p/3/accion", data={"accion": "restaurar", "vista": "fila"}, headers=HX)
    assert "3 sin revisar" in client.get("/").text


def test_favorite_notes_contacted_multiuser(client):
    client.post("/p/1/accion", data={"accion": "favorito", "vista": "fila"}, headers=HX)
    client.post("/p/1/accion", data={"accion": "contactada", "vista": "fila"}, headers=HX)
    assert client.post("/p/1/notas", data={"notas": "llamar el lunes"}, headers=HX).status_code == 204
    assert client.post("/p/1/puntaje", data={"valor": 4}, headers=HX).status_code == 200
    client.post("/p/1/etiquetas", data={"etiquetas": "Patio, luminoso"}, headers=HX)
    # otro usuario ve el estado compartido
    r = client.get("/lista?estado=favoritas", headers={"X-authentik-username": "beto"})
    assert "1 resultados" in r.text and "llamar el lunes" in r.text
    assert "#patio" in client.get("/lista").text
    assert "ana" in client.get("/p/1").text
    assert "1 resultados" in client.get("/lista?estado=contactadas").text
    r = client.post("/p/1/puntaje", data={"valor": 2}, headers=HX)
    assert r.status_code == 200 and r.text.count('class="on"') == 2


def test_filters_price_drop_map_export(client):
    assert "2 resultados" in client.get("/lista?barrio=Almagro").text
    assert "1 resultados" in client.get("/lista?pmin=110000").text
    assert "3 resultados" in client.get("/lista?baja=1").text
    assert len(client.get("/api/mapa").json()) == 2           # sólo las que tienen coordenadas
    assert len(client.get("/api/mapa?pmax=95000").json()) == 1
    csv = client.get("/export.csv").text
    assert csv.count("\n") == 4
    assert "-9.1" in client.get("/cambios").text or "9.1%" in client.get("/cambios").text


def test_compare_defaults_to_favorites(client):
    client.post("/p/1/accion", data={"accion": "favorito", "vista": "fila"}, headers=HX)
    assert "Calle 1" in client.get("/comparar").text
    assert "Calle 2" in client.get("/comparar?ids=1,2").text


def test_compare_uses_total_m2_fallback(client):
    from sqlalchemy import create_engine, text
    import os
    e = create_engine(f"sqlite:///{os.environ['INMO_DB']}")
    with e.begin() as c:
        c.execute(text("UPDATE publicaciones SET m2_totales=50, m2_cubiertos=NULL"))
    html = client.get("/comparar?ids=1,2").text
    assert "2.000" in html                         # 100000 / 50 USD/m²


FAKE = """
import sys, time
from inmo.models import make_engine
from inmo.progress import Tracker
t = Tracker(make_engine(sys.argv[1]), int(sys.argv[2]))
t.update(zonas_total=2, zona_idx=2, zona_actual='palermo', pagina=1, paginas=2, resultados=7)
time.sleep({sleep})
t.finish('ok', '7 avisos')
"""


def _fake_cmd(monkeypatch, sleep):
    import sys
    import app.scrapper_ctl as ctl
    from app import config
    monkeypatch.setattr(ctl, "build_cmd", lambda rid, portal, zonas, skip: [
        sys.executable, "-c", FAKE.format(sleep=sleep), config.DB_PATH, str(rid)])
    return ctl


def _wait(client, cond, tries=60):
    import time
    for _ in range(tries):
        t = client.get("/scrapper/estado").text
        if cond(t):
            return t
        time.sleep(0.25)
    raise AssertionError(t)


def test_scrapper_run_progress_and_finish(client, monkeypatch):
    _fake_cmd(monkeypatch, 1.5)
    page = client.get("/estado").text
    assert "Ejecutar ahora" in page and 'value="recoleta"' in page and page.count('name="zonas"') == 3  # zonas únicas
    r = client.post("/scrapper/iniciar", data={"portal": "zonaprop", "zonas": ["palermo"]}, headers=HX)
    assert r.status_code == 200 and "Scrapper corriendo" in r.text
    assert "corriendo" in client.get("/lista").text                        # indicador global en la barra
    t = _wait(client, lambda t: "Zona 2/2" in t)
    assert "Zona 2/2" in t and "página 1/2" in t and "7 avisos hallados" in t and 'value="75"' in t
    r = client.post("/scrapper/iniciar", data={"portal": "zonaprop"}, headers=HX)
    assert "Ya hay una corrida en curso" in r.text                          # una sola a la vez
    t = _wait(client, lambda t: "Última corrida" in t)
    assert "completa" in t and "7 avisos" in t
    assert client.get("/scrapper/estado?run=1", headers=HX).headers.get("HX-Refresh") == "true"


def test_scrapper_cancel_and_permissions(client, monkeypatch):
    ctl = _fake_cmd(monkeypatch, 30)
    client.post("/scrapper/iniciar", data={"portal": "zonaprop"}, headers=HX)
    r = client.post("/scrapper/cancelar", headers=HX)
    assert "cancelada" in r.text
    monkeypatch.setattr("app.config.RUN_ALLOWED_USERS", {"admin"})
    assert client.post("/scrapper/iniciar", data={"portal": "zonaprop"}, headers=HX).status_code == 403
    assert "no tiene permiso" in client.get("/estado").text
    assert client.post("/scrapper/iniciar", data={"portal": "nope"}, headers={**HX, "X-authentik-username": "admin"}).status_code == 200


def test_scrapper_rejects_unknown_zone(client):
    r = client.post("/scrapper/iniciar", data={"portal": "zonaprop", "zonas": ["../x"]}, headers=HX)
    assert "Zona desconocida" in r.text
