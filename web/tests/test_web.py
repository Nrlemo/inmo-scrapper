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


# ---------- autenticación opcional ----------
def test_auth_disabled_mode(client, monkeypatch):
    monkeypatch.setattr("app.config.AUTH_MODE", "none")
    r = client.get("/estado", headers={"X-authentik-username": ""})
    assert r.status_code == 200 and "Autenticación desactivada" in r.text and "anonimo" in r.text
    monkeypatch.setattr("app.config.PROXY_SECRET", "s3cret")            # en este modo no se exige el secreto
    assert client.get("/", headers={"X-authentik-username": ""}).status_code == 200
    assert "ana" in client.get("/estado").text                          # si el proxy manda identidad, se respeta
    client.post("/p/1/accion", data={"accion": "favorito", "vista": "fila"}, headers={**HX, "X-authentik-username": ""})
    assert "anonimo" in client.get("/p/1", headers={"X-authentik-username": ""}).text


def test_auth_enabled_by_default_shows_no_warning(client):
    assert "Autenticación desactivada" not in client.get("/estado").text


# ---------- programador diario ----------
from datetime import datetime, timedelta  # noqa: E402


def _sched(client):
    import app.scheduler as sch
    from app.main import ENGINE
    return sch, ENGINE


def test_calcular_proxima(client):
    sch, _ = _sched(client)
    now = datetime(2026, 1, 1, 22, 0)
    assert sch.calcular_proxima("03:00", now, rnd=lambda: 0) == datetime(2026, 1, 2, 3, 0)
    assert sch.calcular_proxima("03:00", now, rnd=lambda: 0.5) == datetime(2026, 1, 2, 3, 15)
    assert sch.calcular_proxima("23:30", now, rnd=lambda: 0) == datetime(2026, 1, 1, 23, 30)   # hoy, aún no pasó
    assert sch.calcular_proxima("21:00", now, rnd=lambda: 0) == datetime(2026, 1, 2, 21, 0)    # hoy ya pasó


def test_tick_launches_once_and_reschedules(client):
    sch, eng = _sched(client)
    from sqlalchemy.orm import Session
    lanzadas = []
    fake = lambda engine, user, portal, zonas, gap: lanzadas.append((user, portal, zonas, gap))  # noqa: E731
    with Session(eng) as s:
        p = sch.configurar(s, True, "03:00", "ana", now=datetime(2026, 1, 1, 22, 0))
        prox = p.proxima
    sch.tick(eng, now=prox - timedelta(minutes=1), iniciar=fake)
    assert lanzadas == []                                                # todavía no es la hora
    sch.tick(eng, now=prox + timedelta(seconds=5), iniciar=fake)
    assert lanzadas == [("programada", "zonaprop", [], False)]
    sch.tick(eng, now=prox + timedelta(seconds=40), iniciar=fake)
    assert len(lanzadas) == 1                                            # no se repite
    with Session(eng) as s:
        p = sch.obtener(s)
        assert p.proxima > prox + timedelta(hours=20) and p.ultima_auto is not None


def test_tick_skips_when_overdue_and_retries_when_busy(client):
    sch, eng = _sched(client)
    from sqlalchemy.orm import Session
    import app.scrapper_ctl as ctl
    lanzadas = []
    with Session(eng) as s:
        prox = sch.configurar(s, True, "03:00", "ana", now=datetime(2026, 1, 1, 22, 0)).proxima
    sch.tick(eng, now=prox + timedelta(hours=5), iniciar=lambda *a: lanzadas.append(a))   # web caída: se salta
    assert lanzadas == []
    with Session(eng) as s:
        prox2 = sch.obtener(s).proxima
    estado = {"ocupado": True}

    def busy_then_ok(*a):
        if estado["ocupado"]:
            raise ctl.Ocupado()
        lanzadas.append(a)
    sch.tick(eng, now=prox2 + timedelta(seconds=1), iniciar=busy_then_ok)
    assert lanzadas == []
    estado["ocupado"] = False
    sch.tick(eng, now=prox2 + timedelta(seconds=40), iniciar=busy_then_ok)   # reintenta en el siguiente tick
    assert len(lanzadas) == 1


def test_disable_clears_schedule(client):
    sch, eng = _sched(client)
    from sqlalchemy.orm import Session
    with Session(eng) as s:
        sch.configurar(s, True, "03:00", "ana")
        p = sch.configurar(s, False, "03:00", "ana")
        assert not p.activa and p.proxima is None
    hits = []
    sch.tick(eng, now=datetime.now() + timedelta(days=3), iniciar=lambda *a: hits.append(a))
    assert hits == []


def test_programacion_routes(client):
    page = client.get("/estado").text
    assert "Ejecución automática diaria" in page and "Desactivada" in page
    r = client.post("/scrapper/programacion", data={"activa": "1", "hora": "03:00"}, headers=HX)
    assert r.status_code == 200 and "Activada" in r.text and "Próxima corrida" in r.text
    assert "Activada" in client.get("/estado").text                       # persiste
    r = client.post("/scrapper/programacion", data={"activa": "1", "hora": "25:99"}, headers=HX)
    assert "Hora inválida" in r.text
    r = client.post("/scrapper/programacion", data={"hora": "03:00"}, headers=HX)   # checkbox sin marcar
    assert "Desactivada" in r.text and "No hay corridas automáticas" in r.text


def test_programacion_permissions(client, monkeypatch):
    monkeypatch.setattr("app.config.RUN_ALLOWED_USERS", {"admin"})
    r = client.post("/scrapper/programacion", data={"activa": "1", "hora": "03:00"}, headers=HX)
    assert r.status_code == 403
    assert "no puede cambiar" in client.get("/estado").text


# ---------- páginas de error ----------
def test_auth_errors_show_gif_page_with_back_button(client):
    r = client.get("/", headers={"X-authentik-username": ""})              # sin identidad -> 401
    assert r.status_code == 401
    assert "/static/dennis.gif" in r.text and "Volver" in r.text and "Acceso no autorizado" in r.text
    r = client.post("/p/1/accion", data={"accion": "favorito"})            # sin HX-Request -> 403
    assert r.status_code == 403 and "/static/dennis.gif" in r.text and "Acceso denegado" in r.text
    assert client.get("/static/dennis.gif").status_code == 200


def test_other_errors_use_same_page_without_gif(client):
    r = client.get("/no-existe")
    assert r.status_code == 404 and "Volver" in r.text and "dennis.gif" not in r.text
    assert "<script" not in client.get("/p/999").text                        # el detalle va escapado
