import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scrapper" / "src"))


def _build(tmp_path, monkeypatch, mode, headers=None, base_url="http://testserver"):
    db = tmp_path / "t.sqlite"
    monkeypatch.setenv("INMO_DB", str(db))
    cfg = tmp_path / "profiles.yaml"
    cfg.write_text("profiles:\n  - name: p\n    portals:\n      zonaprop:\n        search_url_template: x\n"
                   "        zones: [almagro, {zone: almagro, price_min: 1}, palermo, recoleta]\n")
    monkeypatch.setenv("INMO_CONFIG", str(cfg))
    monkeypatch.delenv("RUN_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("AUTH_MODE", mode)
    monkeypatch.setenv("SETUP_TOKEN", "TEST-CODE-1234")
    monkeypatch.delenv("PROXY_SECRET", raising=False)
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    from inmo.models import Categorizacion, HistorialPrecio, Publicacion, make_engine
    from sqlalchemy.orm import Session
    eng = make_engine(str(db))
    now = datetime.now()
    with Session(eng) as s:
        for i, (barrio, precio, lat) in enumerate([("Almagro", 100000, -34.6), ("Almagro", 90000, -34.61), ("Palermo", 120000, None)], 1):
            p = Publicacion(portal="zonaprop", id_externo=str(i), url=f"https://x/{i}", titulo=f"Depto <b>{i}</b>",
                            direccion=f"Calle {i}", barrio=barrio, precio=precio, moneda="USD", ambientes=3,
                            m2_cubiertos=50, fotos=["https://img/1.jpg"], fecha_primera_vista=now, fecha_ultima_vista=now,
                            activa=True, consultas_sin_ver=0, lat=lat, lng=-58.4 if lat else None)
            p.categorizacion = Categorizacion(estado="nuevo", etiquetas=[], fecha_modificacion=now)
            p.historial.append(HistorialPrecio(precio=precio * 1.1, moneda="USD", fecha=now - timedelta(days=3)))
            p.historial.append(HistorialPrecio(precio=precio, moneda="USD", fecha=now, variacion_pct=-9.09))
            s.add(p)
        s.commit()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers=headers or {}, base_url=base_url, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Modo authentik: la identidad llega por cabecera (usuario «ana»)."""
    yield from _build(tmp_path, monkeypatch, "authentik", {"X-authentik-username": "ana"})


@pytest.fixture()
def basic(tmp_path, monkeypatch):
    """Modo basic (formulario propio), sin ningún usuario creado."""
    yield from _build(tmp_path, monkeypatch, "basic")


@pytest.fixture()
def basic_https(tmp_path, monkeypatch):
    yield from _build(tmp_path, monkeypatch, "basic", base_url="https://testserver")
