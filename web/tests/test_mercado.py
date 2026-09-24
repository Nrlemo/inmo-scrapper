from datetime import datetime

HX = {"HX-Request": "true"}


def _agregar(n0, barrio, precios, ambientes=3, m2=50):
    """Publicaciones activas en USD (m² totales), ids externos desde n0."""
    from sqlalchemy.orm import Session
    from inmo.models import Categorizacion, Publicacion
    from app.main import ENGINE
    now = datetime.now()
    with Session(ENGINE) as s:
        for k, precio in enumerate(precios):
            p = Publicacion(portal="zonaprop", id_externo=f"m{n0 + k}", url=f"https://x/m{n0 + k}", titulo=f"Aviso {n0 + k}",
                            direccion=f"Calle {n0 + k}", barrio=barrio, precio=precio, moneda="USD", ambientes=ambientes,
                            m2_totales=m2, fotos=[], fecha_primera_vista=now, fecha_ultima_vista=now, activa=True,
                            consultas_sin_ver=0)
            p.categorizacion = Categorizacion(estado="nuevo", etiquetas=[], fecha_modificacion=now)
            s.add(p)
        s.commit()


def test_subbarrios_se_agrupan():
    from app.mercado import grupos
    g = grupos(["Almagro", "Almagro Norte", "Almagro Sur", "Palermo Soho", "Palermo", "Villa Crespo", "Las Cañitas"])
    assert g["Almagro Norte"] == g["Almagro Sur"] == "Almagro" and g["Palermo Soho"] == "Palermo"
    assert g["Villa Crespo"] == "Villa Crespo" and g["Las Cañitas"] == "Las Cañitas"


def test_calculo_por_ambientes_barrio_y_muestra_minima():
    from app.mercado import calcular
    filas = [(i, "Boedo", 3, v) for i, v in enumerate([1000, 1100, 1200, 1300, 1400])]       # mediana 1200 (3 amb.)
    filas += [(10 + i, "Boedo Norte", 4, v) for i, v in enumerate([2000, 2000])]            # 4 amb.: muestra chica
    filas += [(20, "Chico", 3, 999)]                                                        # barrio sin muestra
    vs, stats = calcular(filas)
    assert vs[0] == (-16.7, "Boedo 3 amb.", 5) and vs[2][0] == 0.0
    assert vs[10][1] == "Boedo" and vs[10][2] == 7                    # sin muestra por ambientes: usa el barrio
    assert 20 not in vs                                               # sin referencia: sin badge
    assert {s["barrio"]: s["n"] for s in stats} == {"Boedo": 7, "Chico": 1}


def test_badge_filtro_y_orden_en_la_lista(client):
    _agregar(100, "Boedo", [60000, 70000, 80000, 90000, 100000])   # USD/m²: 1200 1400 1600 1800 2000 → mediana 1600
    t = client.get("/lista?barrio=Boedo").text
    assert "−25 % vs Boedo 3 amb." in t and "+25 % vs Boedo 3 amb." in t and "≈ Boedo 3 amb." in t
    assert 'class="badge vb baja"' in t and 'class="badge vb alta"' in t
    assert "vs Almagro" not in client.get("/lista?barrio=Almagro").text
    bajo = client.get("/lista?barrio=Boedo&bajo=1").text
    assert "Calle 100" in bajo and "Calle 101" in bajo and "Calle 102" not in bajo and "Calle 104" not in bajo
    orden = client.get("/lista?barrio=Boedo&orden=oportunidad").text
    assert orden.index("Calle 100") < orden.index("Calle 102") < orden.index("Calle 104")


def test_errores_de_carga_no_llevan_badge_ni_mueven_la_mediana(client):
    _agregar(400, "Boedo", [60000, 70000, 80000, 90000, 100000])
    _agregar(500, "Boedo", [120000], m2=5400)                  # USD 22/m²: m² mal cargados en el portal
    t = client.get("/lista?barrio=Boedo&orden=oportunidad").text
    assert "−99" not in t and "−25 % vs Boedo 3 amb." in t      # la mediana sigue siendo 1600


def test_se_recalcula_cuando_cambian_los_datos(client):
    _agregar(200, "Boedo", [60000, 70000, 80000, 90000, 100000])
    assert "−25 % vs Boedo 3 amb." in client.get("/lista?barrio=Boedo").text
    from sqlalchemy import text
    from app.main import ENGINE
    with ENGINE.begin() as c:                                   # baja de precio del más barato: 60000 -> 40000
        c.execute(text("UPDATE publicaciones SET precio = 40000 WHERE id_externo = 'm200'"))
    assert "−50 % vs Boedo 3 amb." in client.get("/lista?barrio=Boedo").text


def test_tarjeta_detalle_comparar_y_csv(client):
    _agregar(300, "Boedo", [60000, 70000, 80000, 90000, 100000])
    t = client.get("/lista?barrio=Boedo&orden=oportunidad").text
    import re
    pid = re.search(r'href="/p/(\d+)"', t).group(1)
    assert "−25 % vs Boedo 3 amb." in client.get(f"/p/{pid}").text
    assert "−25 % vs Boedo 3 amb." in client.get(f"/?despues={int(pid) + 1}", headers=HX).text   # tarjeta de Revisión
    comp = client.get(f"/comparar?ids={pid}").text
    assert "-25%" in comp and "vs Boedo 3 amb." in comp and ">Boedo</a>" in comp
    csv = client.get("/export.csv?barrio=Boedo").text
    assert "vs_barrio_pct" in csv.splitlines()[0] and ",-25.0," in csv
