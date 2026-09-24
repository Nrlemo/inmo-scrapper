HX = {"HX-Request": "true"}


def test_requires_identity(client):
    r = client.get("/", headers={"X-authentik-username": ""})
    assert r.status_code == 401


def test_post_requires_htmx_header(client):
    assert client.post("/p/1/accion", data={"accion": "favorito"}).status_code == 403


def test_all_pages_render(client):
    for url in ["/", "/lista", "/lista?estado=potencial", "/ranking", "/cambios", "/comparar", "/mapa", "/estado", "/p/1", "/lista?q=Calle&baja=1&nuevas=1&orden=usd_m2"]:
        r = client.get(url)
        assert r.status_code == 200, url
    assert "&lt;b&gt;" in client.get("/p/1").text          # escapado de contenido scrapeado


def test_selector_de_tema(client):
    t = client.get("/lista").text
    assert 'id="tema"' in t and "localStorage.getItem('inmo-tema')" in t
    assert t.index("inmo-tema") < t.index("app.css")                  # se aplica antes de pintar (sin parpadeo)
    assert 'class="logo logo-c"' in t and 'class="logo logo-o"' in t   # el logo sigue al tema elegido, no sólo al dispositivo


def test_revision_mini_mapa_o_sin_geolocalizacion(client):
    t = client.get("/").text                                      # la cola empieza por la 3, que no tiene coordenadas
    assert "/static/leaflet.js" in t and "Sin geolocalización" in t and 'class="mini-mapa"' not in t
    t = client.get("/?despues=3", headers=HX).text                # la 2 sí tiene
    assert 'data-lat="-34.61" data-lng="-58.4"' in t and "Sin geolocalización" not in t
    assert "google.com/maps/search/?api=1&amp;query=-34.61,-58.4" in t


def test_review_flow_and_discard(client):
    assert "<b>3</b> sin revisar" in client.get("/").text
    r = client.post("/p/3/accion", data={"accion": "descartar", "vista": "card"}, headers=HX)
    assert r.status_code == 200
    assert "<b>2</b> sin revisar" in client.get("/").text
    assert "3 resultados" not in client.get("/lista?estado=activas").text
    assert "1 resultados" in client.get("/lista?estado=descartadas").text
    client.post("/p/3/accion", data={"accion": "restaurar", "vista": "fila"}, headers=HX)
    assert "<b>3</b> sin revisar" in client.get("/").text


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


def test_potencial(client):
    r = client.post("/p/2/accion", data={"accion": "potencial", "vista": "fila"}, headers=HX)
    assert "◆ potencial" in r.text
    r = client.get("/lista?estado=potencial", headers={"X-authentik-username": "beto"})   # compartido
    assert "1 resultados" in r.text and "Calle 2" in r.text
    assert [d["pot"] for d in client.get("/api/mapa?estado=potencial").json()] == [True]
    client.post("/p/2/accion", data={"accion": "descartar", "vista": "fila"}, headers=HX)  # descartar la saca
    assert "0 resultados" in client.get("/lista?estado=potencial").text
    client.post("/p/2/accion", data={"accion": "potencial", "vista": "card"}, headers=HX)  # y marcarla la restaura
    assert "1 resultados" in client.get("/lista?estado=potencial").text
    assert "0 resultados" in client.get("/lista?estado=descartadas").text


def test_puntaje_por_usuario_y_ranking(client):
    BETO = {**HX, "X-authentik-username": "beto"}
    client.post("/p/1/puntaje", data={"valor": 5}, headers=HX)
    r = client.post("/p/1/puntaje", data={"valor": 2}, headers=BETO)
    assert r.text.count('class="on"') == 2 and "Promedio <b>3.5</b>" in r.text   # beto ve el suyo, no el de ana
    assert client.get("/p/1").text.count('class="on"') >= 5                       # ana sigue viendo 5
    client.post("/p/2/puntaje", data={"valor": 4}, headers=HX)
    r = client.get("/ranking")
    assert r.text.index("Calle 2") < r.text.index("Calle 1")                       # promedio 4 > 3.5
    r = client.get("/ranking?usuario=ana")
    assert r.text.index("Calle 1") < r.text.index("Calle 2")                       # ana: 5 > 4
    assert "Calle 2" not in client.get("/ranking?usuario=beto").text
    assert "Calle 3" not in client.get("/ranking").text                            # sin puntaje
    r = client.get("/lista?orden=puntaje")
    assert r.text.index("Calle 2") < r.text.index("Calle 1") < r.text.index("Calle 3")
    client.post("/p/1/puntaje", data={"valor": 0}, headers=HX)                     # ana borra el suyo
    assert "★ 2,0" in client.get("/lista").text
    from sqlalchemy import text
    with client.app.state.engine.connect() as c:
        assert c.execute(text("SELECT puntaje FROM categorizacion WHERE publicacion_id=1")).scalar() == 2


def test_migra_puntajes_viejos(tmp_path, monkeypatch):
    """Una base con puntajes por publicación (esquema anterior) los pasa a quien los puso."""
    import sqlite3
    from conftest import _build
    gen = _build(tmp_path, monkeypatch, "authentik", {"X-authentik-username": "ana"})
    client = next(gen)
    gen.close()
    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""DROP TABLE web_puntajes; ALTER TABLE web_revision DROP COLUMN potencial;
        UPDATE categorizacion SET puntaje=4 WHERE publicacion_id=1;
        UPDATE categorizacion SET puntaje=3 WHERE publicacion_id=2;
        INSERT INTO web_eventos (publicacion_id, usuario, tipo, detalle, fecha) VALUES (1, 'carla', 'puntaje', '4', '2026-01-01');""")
    con.commit(); con.close()
    for m in [m for m in list(__import__("sys").modules) if m == "app" or m.startswith("app.")]:
        del __import__("sys").modules[m]
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers={"X-authentik-username": "ana"}) as c:
        r = c.get("/ranking")
        assert "carla" in r.text and "anterior" in r.text and "Calle 1" in r.text and "Calle 2" in r.text
        assert c.post("/p/1/accion", data={"accion": "potencial"}, headers=HX).status_code == 200


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


# ---------- ronda por navegador en la pantalla Estado ----------
def _ronda_en_curso():
    from sqlalchemy.orm import Session
    from inmo import navegador
    from inmo.config import load_config
    from app import config
    from app.main import ENGINE
    with Session(ENGINE) as s:
        return navegador.iniciar(s, load_config(config.CONFIG_PATH), "ana")["ronda"]


def test_estado_sin_acciones_del_scrapper_viejo(client):
    page = client.get("/estado").text
    assert "Todavía no hubo ninguna ronda" in page and "Correr ronda ahora" in page
    assert "Ejecutar ahora" not in page and "Ejecución automática diaria" not in page
    for ruta in ("/scrapper/iniciar", "/scrapper/programacion", "/scrapper/cancelar"):
        assert client.post(ruta, headers=HX).status_code in (404, 405)


def test_estado_muestra_la_ronda_en_curso_y_permite_cancelarla(client):
    rid = _ronda_en_curso()
    page = client.get("/estado").text
    assert "Ronda en curso" in page and f'hx-get="/ronda/estado?run={rid}"' in page and "Zona 1/4" in page
    assert "corriendo" in client.get("/lista").text                        # indicador global en la barra
    r = client.post("/ronda/cancelar", headers=HX)
    assert r.status_code == 200 and "cancelada" in r.text and "Ronda en curso" not in r.text
    assert client.get(f"/ronda/estado?run={rid}", headers=HX).headers.get("HX-Refresh") == "true"


def test_al_arrancar_solo_se_cortan_las_corridas_del_scrapper_viejo(client):
    from datetime import datetime
    from sqlalchemy.orm import Session
    from inmo.models import Ejecucion
    from app import rondas
    from app.main import ENGINE
    rid = _ronda_en_curso()
    with Session(ENGINE) as s:
        now = datetime.now()
        s.add(Ejecucion(portal="zonaprop", zonas=[], inicio=now, actualizado=now, estado="corriendo", pid=123))
        s.commit()
        rondas.marcar_huerfanas(s)
        estados = {e.id: e.estado for e in s.query(Ejecucion)}
    assert estados[rid] == "corriendo" and sorted(estados.values()) == ["corriendo", "interrumpida"]


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


def test_estado_without_profiles_yaml_shows_hint(client, monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.CONFIG_PATH", str(tmp_path / "no-existe.yaml"))
    r = client.get("/estado")
    assert r.status_code == 200 and "Todavía no hay zonas de búsqueda" in r.text and "Correr ronda ahora" not in r.text


def test_unwritable_data_dir_gives_clear_error(tmp_path, monkeypatch):
    import os
    import pytest
    if os.getuid() == 0:
        pytest.skip("root ignora los permisos")
    monkeypatch.setenv("INMO_DB", str(tmp_path / "ro" / "inmo.sqlite"))
    (tmp_path / "ro").mkdir()
    (tmp_path / "ro").chmod(0o500)
    try:
        from app.db import verificar_datos
        with pytest.raises(RuntimeError) as e:
            verificar_datos(str(tmp_path / "ro" / "inmo.sqlite"))
        assert "chown" in str(e.value) and "PUID" in str(e.value)
    finally:
        (tmp_path / "ro").chmod(0o700)


def test_seed_profiles_from_example(tmp_path, monkeypatch):
    import os
    import pytest
    from app import config
    from app.db import sembrar_config
    ejemplo = tmp_path / "ejemplo.yaml"
    ejemplo.write_text("profiles: []  # ejemplo\n")
    destino = tmp_path / "config" / "profiles.yaml"
    monkeypatch.setattr(config, "CONFIG_EXAMPLE", str(ejemplo))
    monkeypatch.setattr(config, "CONFIG_PATH", str(destino))
    assert sembrar_config() is True and destino.read_text() == "profiles: []  # ejemplo\n"     # crea carpeta y archivo
    destino.write_text("profiles: [mio]\n")
    assert sembrar_config() is False and destino.read_text() == "profiles: [mio]\n"            # nunca pisa el existente
    monkeypatch.setattr(config, "CONFIG_EXAMPLE", str(tmp_path / "no-hay.yaml"))
    destino.unlink()
    assert sembrar_config() is False and not destino.exists()                                  # sin ejemplo: no hace nada
    if os.getuid() != 0:                                                                        # carpeta sin permiso: avisa, no falla
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        monkeypatch.setattr(config, "CONFIG_EXAMPLE", str(ejemplo))
        monkeypatch.setattr(config, "CONFIG_PATH", str(ro / "profiles.yaml"))
        try:
            assert sembrar_config() is False
        finally:
            ro.chmod(0o700)


def test_etiquetas_automaticas_filtro_y_badges(client):
    from sqlalchemy import text
    from app.main import ENGINE
    with ENGINE.begin() as c:
        c.execute(text("""UPDATE categorizacion SET etiquetas_auto='["patio"]' WHERE publicacion_id=1"""))
        c.execute(text("""UPDATE categorizacion SET etiquetas='["patio","ver"]' WHERE publicacion_id=2"""))
    html = client.get("/lista").text
    assert '<option value="patio"' in html and "#patio (2)" in html and "#ver (1)" in html
    r = client.get("/lista?etiqueta=patio").text
    assert "Calle 1" in r and "Calle 2" in r and "Calle 3" not in r
    assert 'class="badge auto"' in r                                      # la 1 la tiene sólo automática
    assert r.count('title="Automática') == 1                              # la 2 la tiene manual: no se repite
    assert "Calle 1" not in client.get("/lista?etiqueta=ver").text


def test_galeria_en_revision_y_miniaturas_chicas(client):
    import json
    from sqlalchemy import text
    from app.main import ENGINE
    fotos = [f"https://img/avisos/1/720x532/{i}.jpg" for i in range(3)]
    with ENGINE.begin() as c:
        c.execute(text("UPDATE publicaciones SET fotos=:f"), {"f": json.dumps(fotos)})
    html = client.get("/").text
    assert "data-fotos=" in html and ">1/3<" in html and html.count('<i class') >= 3
    assert "https://img/avisos/1/720x532/0.jpg" in html                    # la tarjeta usa la foto grande
    lista = client.get("/lista").text
    assert "/360x266/0.jpg" in lista and "/720x532/" not in lista            # el listado, la miniatura


def test_api_ronda_por_navegador_con_token(client):
    import re
    from pathlib import Path
    html = (Path(__file__).parents[2] / "scrapper" / "tests" / "fixtures" / "zonaprop_listado.html").read_text(encoding="utf-8")
    html = html.replace("2.864", "25").replace("2864", "25")
    assert client.get("/api/navegador/ping").status_code == 401
    assert client.get("/api/navegador/ping", headers={"Authorization": "Bearer inventado"}).status_code == 401
    r = client.post("/navegador/token", headers={"HX-Request": "true"})
    token = re.search(r'id="tok-nuevo">([^<]+)<', r.text).group(1)
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/navegador/ping", headers=auth).json() == {"ok": True, "usuario": "ana"}
    r = client.post("/api/navegador/ronda", headers=auth).json()
    assert r["url"] == "x" and r["ronda"]
    r = client.post("/api/navegador/pagina", headers=auth, json={"ronda": r["ronda"], "url": "x", "html": html}).json()
    assert r["siguiente"] == "x" and r["pausa"] >= 30          # próxima zona (el YAML de prueba usa la plantilla "x")
    estado = client.get("/estado").text
    assert "Ronda por navegador" in estado and "token activo" in estado and "ana (navegador)" in estado
    client.post("/navegador/token", headers={"HX-Request": "true"})         # regenerar invalida el anterior
    assert client.get("/api/navegador/ping", headers=auth).status_code == 401


# ---------- filtros de búsqueda (Estado → Búsqueda) ----------
FORM = {"nombre": "p", "precio_min": "95000", "precio_max": "200.000", "amb_min": "3", "excluir": "pozo, sin escritura",
        "zonaprop_activo": "1", "zonaprop_zonas": "almagro\npalermo 100000-", "zonaprop_plantilla": ""}


def test_busqueda_se_importa_del_yaml_y_se_ve_en_estado(client):
    t = client.get("/estado").text
    assert "🔎 Búsqueda" in t and "Guardar filtros" in t and "importado de profiles.yaml" in t
    assert "almagro 1-" in t and "recoleta" in t                             # zonas del YAML de prueba
    assert "Argenprop · se incorpora en la etapa 2" in t


def test_guardar_filtros_oculta_lo_que_no_cumple_y_cambia_la_ronda(client):
    r = client.post("/busqueda/0", data=FORM, headers=HX)
    assert r.status_code == 200 and "Guardado. 1 aviso activo queda fuera" in r.text          # la 2 cuesta 90000 < 95000
    assert "departamentos-venta-palermo-mas-de-3-ambientes-100000-200000-dolar.html" in r.text
    lista = client.get("/lista").text
    assert "/p/2" not in lista and "/p/1" in lista
    assert "/p/2" in client.get("/lista?fuera=1").text and "fuera de filtros" in client.get("/lista?fuera=1").text
    assert "<b>2</b> sin revisar" in client.get("/").text                       # la cola tampoco la muestra
    client.post("/p/2/accion", data={"accion": "favorito", "vista": "fila"}, headers=HX)
    assert "/p/2" in client.get("/lista").text                                   # lo que marcaste se ve siempre
    # La ronda por navegador recorre las búsquedas nuevas
    import re
    token = re.search(r'id="tok-nuevo">([^<]+)<', client.post("/navegador/token", headers=HX).text).group(1)
    r = client.post("/api/navegador/ronda", headers={"Authorization": f"Bearer {token}"}).json()
    assert r["url"] == "https://www.zonaprop.com.ar/departamentos-venta-almagro-mas-de-3-ambientes-95000-200000-dolar.html"
    # Aflojar los filtros la devuelve
    client.post("/busqueda/0", data={**FORM, "precio_min": "50000"}, headers=HX)
    assert "fuera de filtros" not in client.get("/estado").text.split("🔎")[0]


def test_filtros_con_error_no_se_guardan_y_conservan_lo_escrito(client):
    r = client.post("/busqueda/0", data={**FORM, "zonaprop_zonas": "almagro\nvilla crespo"}, headers=HX)
    assert "Zona de la línea 2" in r.text and "villa crespo" in r.text
    r = client.post("/busqueda/0", data={**FORM, "precio_min": "300000"}, headers=HX)
    assert "mínimo de precio es mayor que el máximo" in r.text
    assert "importado de profiles.yaml" in client.get("/estado").text           # nada se guardó


def test_copiar_y_borrar_busquedas(client):
    client.post("/busqueda/0", data=FORM, headers=HX)
    r = client.post("/busqueda/0/copiar", headers=HX)
    assert "Búsqueda copiada" in r.text and ">p-2</button>" in r.text
    r = client.post("/busqueda/1/borrar", headers=HX)
    assert "«p-2» borrada" in r.text
    assert "al menos una" in client.post("/busqueda/0/borrar", headers=HX).text


def test_maximos_por_portal_desde_estado(client):
    r = client.post("/busqueda/0", data={**FORM, "precio_min": "1", "amb_min": "1", "dorm_max": "3", "zonaprop_amb_max": "2"}, headers=HX)
    assert "Guardado. 3 avisos activos quedan fuera" in r.text                    # los 3 de prueba tienen 3 ambientes
    assert 'name="zonaprop_amb_max" inputmode="numeric" value="2"' in r.text and 'name="dorm_max" inputmode="numeric" value="3"' in r.text
    r = client.post("/busqueda/0", data={**FORM, "precio_min": "1", "dorm_min": "3", "dorm_max": "2"}, headers=HX)
    assert "mínimo de dormitorios es mayor" in r.text


def test_m2_maximo_global_y_por_portal(client):
    # los 3 avisos de prueba tienen 50 m² cubiertos
    r = client.post("/busqueda/0", data={**FORM, "precio_min": "1", "m2_cub_max": "40"}, headers=HX)
    assert "Guardado. 3 avisos activos quedan fuera" in r.text
    r = client.post("/busqueda/0", data={**FORM, "precio_min": "1", "m2_cub_max": "", "zonaprop_m2_tot_max": "70"}, headers=HX)
    assert "Guardado. 0 avisos activos" in r.text and 'name="zonaprop_m2_tot_max" inputmode="numeric" value="70"' in r.text
