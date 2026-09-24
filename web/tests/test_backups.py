import gzip
import shutil
import sqlite3
from datetime import datetime, timedelta

HX = {"HX-Request": "true"}


def _pubs(path) -> int:
    return sqlite3.connect(path).execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0]


def test_retencion_abuelo_padre_hijo(client):
    from app.backups import a_conservar
    hoy = datetime(2026, 9, 24, 5, 0)
    fechas = [hoy - timedelta(days=d) for d in range(400)] + [hoy - timedelta(hours=3)]   # 1 por día + uno extra hoy
    q = a_conservar(fechas, 7, 4, 6)
    assert max(fechas) in q                                                # el más nuevo, siempre
    assert {f.date() for f in q if f >= hoy - timedelta(days=6)} == {(hoy - timedelta(days=d)).date() for d in range(7)}
    assert len({f.isocalendar()[:2] for f in q}) >= 4 and len({(f.year, f.month) for f in q}) == 6
    assert len(q) <= 7 + 4 + 6
    assert min(q) >= hoy - timedelta(days=200)                             # nada más viejo que ~6 meses


def test_backup_verificado_comprimido_y_copia_extra(client, tmp_path, monkeypatch):
    from app import backups, config
    monkeypatch.setattr(config, "BACKUP_DIR_EXTRA", str(tmp_path / "otro-disco"))
    e = backups.hacer_backup(datetime(2026, 9, 24, 5, 0))
    assert e["ok"] and e["publicaciones"] == 3 and e["extra"]["ok"]
    gz = tmp_path / "backups" / e["nombre"]
    assert gz.name == "inmo-20260924-050000.sqlite.gz" and (tmp_path / "otro-disco" / gz.name).is_file()
    with gzip.open(gz) as fi, open(tmp_path / "restaurada.sqlite", "wb") as fo:
        shutil.copyfileobj(fi, fo)
    assert _pubs(tmp_path / "restaurada.sqlite") == 3
    assert not [p for p in (tmp_path / "backups").iterdir() if p.name.startswith(".")]   # sin temporales
    assert backups.estado()["ultimo_ok"] == "2026-09-24T05:00:00"


def test_si_falla_la_copia_extra_el_backup_queda(client, monkeypatch, tmp_path):
    from app import backups, config
    (tmp_path / "archivo").write_text("x")
    monkeypatch.setattr(config, "BACKUP_DIR_EXTRA", str(tmp_path / "archivo" / "no-es-carpeta"))
    e = backups.hacer_backup(datetime(2026, 9, 24, 5, 0))
    assert e["ok"] and not e["extra"]["ok"] and len(backups.listar()) == 1


def test_backup_fallido_queda_registrado(client, monkeypatch):
    from app import backups, config
    monkeypatch.setattr(config, "DB_PATH", "/no/existe/inmo.sqlite")
    e = backups.hacer_backup(datetime(2026, 9, 24, 5, 0))
    assert e["ok"] is False and "OperationalError" in e["error"] and backups.listar() == []
    # tras un fallo no se reintenta en cada minuto, sino a la hora
    assert backups.tick(datetime(2026, 9, 24, 5, 30)) is None


def test_rotacion_borra_lo_que_sobra(client, tmp_path):
    from app import backups
    d = tmp_path / "backups"
    d.mkdir()
    hoy = datetime(2026, 9, 24, 5, 0)
    for n in range(60):
        (d / f"inmo-{hoy - timedelta(days=n):%Y%m%d-%H%M%S}.sqlite.gz").write_bytes(b"x")
    (d / "otra-cosa.txt").write_text("no se toca")
    borrados = backups.rotar(d)
    quedan = backups.listar(d)
    assert len(quedan) + len(borrados) == 60 and len(quedan) <= 17 and (d / "otra-cosa.txt").exists()


def test_cuando_toca(client):
    from app.backups import toca
    assert not toca(datetime(2026, 9, 24, 4, 59), None, "05:00")                     # todavía no es la hora
    assert toca(datetime(2026, 9, 24, 5, 0), None, "05:00")                          # nunca se hizo
    assert toca(datetime(2026, 9, 24, 9, 0), "2026-09-23T05:00:00", "05:00")         # el de hoy falta (web apagada a las 5)
    assert not toca(datetime(2026, 9, 24, 9, 0), "2026-09-24T05:00:10", "05:00")     # ya se hizo hoy
    assert not toca(datetime(2026, 9, 25, 3, 0), "2026-09-24T05:00:10", "05:00")


def test_restaurar_como_dice_la_guia(client, tmp_path, monkeypatch):
    """deploy/servidor.md: con la web parada (ninguna conexión abierta), descomprimir el backup sobre la base y borrar
    -wal/-shm. Se prueba sobre una base propia: la del cliente de prueba tiene conexiones abiertas."""
    from app import backups, config
    prod = tmp_path / "prod.sqlite"
    src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(prod)
    src.backup(dst)
    src.close()
    dst.execute("PRAGMA journal_mode=WAL")
    dst.close()
    monkeypatch.setattr(config, "DB_PATH", str(prod))
    e = backups.hacer_backup(datetime(2026, 9, 24, 5, 0))
    c = sqlite3.connect(prod)
    c.execute("DELETE FROM publicaciones")                             # el desastre
    c.commit()
    c.close()
    assert _pubs(prod) == 0
    with gzip.open(tmp_path / "backups" / e["nombre"]) as fi, open(prod, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    for suf in ("-wal", "-shm"):
        (tmp_path / f"prod.sqlite{suf}").unlink(missing_ok=True)
    c = sqlite3.connect(prod)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and _pubs(prod) == 3


def test_panel_ahora_y_descarga(client):
    t = client.get("/estado").text
    assert "💾 Backups" in t and "Todavía no se hizo ningún backup" in t and "BACKUP_DIR_EXTRA" in t
    r = client.post("/backups/ahora", headers=HX)
    assert r.status_code == 200 and "Último backup" in r.text and "verificado" in r.text
    from app import backups
    nombre = backups.listar()[0]["nombre"]
    d = client.get(f"/backups/{nombre}")
    assert d.status_code == 200 and d.headers["content-type"] == "application/gzip" and gzip.decompress(d.content)[:15] == b"SQLite format 3"
    assert client.get("/backups/inmo-20990101-000000.sqlite.gz").status_code == 404
    assert client.get("/backups/estado.json").status_code == 404        # sólo archivos de backup
