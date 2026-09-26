"""#38: coordenadas para avisos que el portal no geolocaliza (copiar del duplicado y geocodificar la dirección)."""
from datetime import datetime

import pytest

HX = {"HX-Request": "true"}


def test_normalizar_direcciones_de_los_portales(client):
    from app.geocodificacion import normalizar
    assert normalizar("Avenida Hipólito Yrigoyen 3800, Piso 3") == "avenida hipolito yrigoyen 3800"
    assert normalizar("Peron  Al 1200") == "peron 1200"                    # «al» = altura de la cuadra
    assert normalizar("AV. Entre Rios al 1600 1°") == "av. entre rios 1600"
    assert normalizar("25 de Mayo 300") == "25 de mayo 300"
    assert normalizar("Humberto 1º 2003") == "humberto 1 2003"            # el «1º» es de la calle, no el piso
    assert normalizar("Matheu") is None and normalizar(None) is None       # sin altura: no se puede
    assert normalizar("Juan Carlos Gomez Al 0") == "juan carlos gomez 1"   # USIG no acepta la altura 0


def _sin_coordenadas(pid, direccion):
    from sqlalchemy import text
    from app.main import ENGINE
    with ENGINE.begin() as c:
        c.execute(text("UPDATE publicaciones SET lat=NULL, lng=NULL, geo_fuente=NULL, direccion=:d WHERE id=:i"),
                  {"d": direccion, "i": pid})


def _pub(pid):
    from sqlalchemy import text
    from app.main import ENGINE
    with ENGINE.connect() as c:
        return c.execute(text("SELECT lat, lng, geo_fuente FROM publicaciones WHERE id=:i"), {"i": pid}).one()


def test_geocodifica_por_direccion_una_sola_vez(client):
    from sqlalchemy.orm import Session
    from app import geocodificacion
    from app.main import ENGINE
    _sin_coordenadas(1, "Bogotá 100, Almagro")
    _sin_coordenadas(2, "Bogota 100")                                       # misma dirección: una sola consulta
    _sin_coordenadas(3, "Calle Inventada 9999")
    pedidos = []

    def falso(q):
        pedidos.append(q)
        return [(-34.61299, -58.432666, "BOGOTA 100, CABA")] if q.startswith("bogota") else []
    with Session(ENGINE) as s:
        r = geocodificacion.geocodificar(s, consultar=falso, dormir=lambda x: None)
    assert sorted(pedidos) == ["bogota 100", "calle inventada 9999"]
    assert r["direccion"] == 2 and r["sin_resultado"] == 1 and r["consultas"] == 2
    assert _pub(1) == (-34.61299, -58.432666, "direccion") and _pub(3) == (None, None, None)
    with Session(ENGINE) as s:                                              # otra tanda: la no encontrada no se repite
        r = geocodificacion.geocodificar(s, consultar=falso, dormir=lambda x: None)
    assert r["consultas"] == 0 and len(pedidos) == 2


def test_error_del_servicio_se_reintenta_en_otra_tanda(client):
    from sqlalchemy.orm import Session
    from app import geocodificacion
    from app.main import ENGINE
    _sin_coordenadas(1, "Mendoza 4900")

    def caido(q):
        raise OSError("timeout")
    with Session(ENGINE) as s:
        assert geocodificacion.geocodificar(s, consultar=caido, dormir=lambda x: None)["errores"] >= 1
        from app.models_web import Geocache
        assert s.query(Geocache).count() == 0                               # no quedó en cache como «no encontrada»
    with Session(ENGINE) as s:
        r = geocodificacion.geocodificar(s, consultar=lambda q: [(-34.57, -58.48, "MENDOZA 4900, CABA")], dormir=lambda x: None)
    assert r["direccion"] >= 1 and _pub(1)[2] == "direccion"


def test_usig_toma_el_primer_resultado_de_caba(client, monkeypatch):
    """Respuesta real de USIG para «Bogota 100» (2026-09-25): el primero es CABA; los demás, otros partidos."""
    import io, json
    from app import geocodificacion
    datos = {"direccionesNormalizadas": [
        {"direccion": "Bogotá 100, Merlo", "nombre_partido": "Merlo", "coordenadas": {"x": "-58.7507024", "y": "-34.6579256"}},
        {"direccion": "BOGOTA 100, CABA", "nombre_partido": "CABA", "coordenadas": {"x": "-58.432666", "y": "-34.612990"}},
        {"direccion": "Bogotà 100, Malvinas Argentinas", "nombre_partido": "Malvinas Argentinas",
         "coordenadas": {"x": "-58.7562703", "y": "-34.4782622"}}]}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    pedidos = []
    monkeypatch.setattr(geocodificacion.urllib.request, "urlopen",
                        lambda req, timeout: pedidos.append(req) or R(json.dumps(datos).encode()))
    assert geocodificacion.usig("bogota 100") == [(-34.61299, -58.432666, "BOGOTA 100, CABA")]
    assert "servicios.usig.buenosaires.gob.ar" in pedidos[0].full_url and pedidos[0].get_header("User-agent").startswith("inmo")
    datos["direccionesNormalizadas"] = datos["direccionesNormalizadas"][:1]  # sólo fuera de CABA: nada
    assert geocodificacion.usig("bogota 100") == []


def test_copia_del_duplicado_antes_de_geocodificar(client):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app import geocodificacion
    from app.main import ENGINE
    _sin_coordenadas(3, "Calle 3")
    with ENGINE.begin() as c:                                               # la 3 es el mismo inmueble que la 1
        c.execute(text("UPDATE publicaciones SET grupo_id=1, geo_fuente='portal' WHERE id IN (1)"))
        c.execute(text("UPDATE publicaciones SET grupo_id=1, portal='mercadolibre' WHERE id=3"))
    with Session(ENGINE) as s:
        r = geocodificacion.geocodificar(s, consultar=lambda q: pytest.fail("no debería consultar"), dormir=lambda x: None)
    assert r["duplicado"] == 1 and _pub(3)[2] == "duplicado" and _pub(3)[:2] == _pub(1)[:2]


def test_mapa_y_tarjeta_marcan_la_ubicacion_aproximada(client):
    from sqlalchemy import text
    from app.main import ENGINE
    with ENGINE.begin() as c:
        c.execute(text("UPDATE publicaciones SET lat=-34.61, lng=-58.43, geo_fuente='direccion' WHERE id=2"))
    puntos = {p["id"]: p for p in client.get("/api/mapa").json()}
    assert puntos[2]["aprox"] is True and puntos[1]["aprox"] is False
    assert "punteado: ubicación aproximada" in client.get("/mapa").text
    assert "Ubicación aproximada" in client.get("/?despues=3", headers=HX).text       # la 2, en la cola
    assert "Ubicación aproximada" not in client.get("/?despues=2", headers=HX).text   # la 1, del portal


def test_entre_varios_candidatos_elige_el_del_barrio():
    """USIG para «san juan 2300» (2026-09-25): la avenida (San Cristóbal) y San Juan Bautista de La Salle (Lugano)."""
    from app.geocodificacion import elegir
    candidatos = [(-34.655017, -58.472702, "SAN JUAN BAUTISTA DE LA SALLE AV. 2300, CABA"),
                  (-34.623297, -58.397679, "SAN JUAN AV. 2300, CABA")]
    san_cristobal = (-34.625, -58.402)
    assert elegir(candidatos, san_cristobal)[2] == "SAN JUAN AV. 2300, CABA"
    assert elegir(candidatos[:1], san_cristobal) is None                    # el único está en otra punta: descartado
    assert elegir(candidatos, None)[2].startswith("SAN JUAN BAUTISTA")      # sin referencia del barrio: el primero
    assert elegir([], san_cristobal) is None


def test_centro_del_barrio_resiste_un_aviso_mal_ubicado(client):
    """Con el promedio, un aviso de San Nicolás ubicado por el portal en Córdoba corría el centro 3 km."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app import geocodificacion
    from app.main import ENGINE
    with ENGINE.begin() as c:
        for i, (lat, lng) in enumerate([(-34.603, -58.380), (-34.604, -58.382), (-34.602, -58.378), (-31.4, -64.3)]):
            c.execute(text("INSERT INTO publicaciones (portal, id_externo, url, barrio, fotos, fecha_primera_vista, "
                           "fecha_ultima_vista, activa, consultas_sin_ver, fuera_filtro, lat, lng, geo_fuente) VALUES "
                           "('zonaprop', :i, 'u', 'San Nicolás', '[]', '2026-01-01', '2026-01-01', 1, 0, 0, :la, :ln, 'portal')"),
                      {"i": f"sn{i}", "la": lat, "ln": lng})
    with Session(ENGINE) as s:
        centro = geocodificacion.centros_de_barrio(s)["san nicolas"]
    assert abs(centro[0] + 34.603) < 0.01 and abs(centro[1] + 58.380) < 0.01
    sarmiento = (-34.604584, -58.375055, "SARMIENTO 600, CABA")              # real (USIG, 2026-09-25)
    assert geocodificacion.elegir([sarmiento], centro) == sarmiento
