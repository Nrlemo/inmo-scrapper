import csv
import importlib.util
import shutil
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("probe", Path(__file__).resolve().parent.parent / "scripts" / "probe_metodos.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

OK = '<html><div data-posting-type="PROPERTY"></div><div data-posting-type="PROPERTY"></div></html>'
NO = "<html><head><title>Just a moment...</title></head></html>"


SEEN: list[dict] = []          # cabeceras de los pedidos recibidos (para comprobar qué manda cada cliente)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append({k.lower(): v for k, v in self.headers.items()})
        code, body = {"/ok": (200, OK), "/bloqueo": (403, NO), "/anomalo": (200, "<html>vacío</html>")}.get(self.path, (404, "x"))
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def base():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


needs_curl = pytest.mark.skipif(not shutil.which("curl"), reason="sin curl")


def test_limits_protect_remote_sites_only():
    assert probe.validar_limites(probe.URL_DEFECTO, 3600, 30) is None
    assert "1800" in probe.validar_limites(probe.URL_DEFECTO, 60, 30)
    assert "20" in probe.validar_limites(probe.URL_DEFECTO, 3600, 5)
    assert probe.validar_limites("http://127.0.0.1:9/x", 0.1, 0) is None          # local: sin mínimos (para pruebas)
    assert probe.validar_limites("ftp://x", 3600, 30)


def test_cli_refuses_impolite_settings_without_any_request(capsys):
    assert probe.main(["--every", "60"]) == 2
    assert "1800" in capsys.readouterr().err


@needs_curl
@pytest.mark.parametrize("metodo", probe.METODOS)
def test_measure_classifies_results(base, metodo):
    ok = probe.medir(metodo, base + "/ok")
    assert (ok["resultado"], ok["codigo"], ok["avisos"]) == ("OK", 200, 2)
    b = probe.medir(metodo, base + "/bloqueo")
    assert (b["resultado"], b["codigo"]) == ("BLOQUEO", 403)
    assert probe.medir(metodo, base + "/anomalo")["resultado"] == "ANOMALO"       # 200 pero sin avisos
    assert probe.medir(metodo, base + "/otra")["resultado"] == "ERROR"            # 404
    e = probe.medir(metodo, "http://127.0.0.1:1/x", timeout=3)
    assert e["resultado"] == "ERROR" and e["error"]                               # sin conexión: se registra, no explota


@needs_curl
def test_cycle_writes_csv_with_both_methods_and_pauses_between(base, tmp_path):
    out, pausas = tmp_path / "m.csv", []
    probe.ciclo(base + "/ok", 1, out, 30, sleep=pausas.append)
    probe.ciclo(base + "/bloqueo", 2, out, 30, sleep=pausas.append)
    filas = list(csv.DictReader(open(out, encoding="utf-8")))
    assert len(filas) == 6 and pausas == [30] * 4               # 3 pedidos por ciclo, 2 pausas entre ellos
    assert {f["metodo"] for f in filas} == set(probe.METODOS)
    assert [f["resultado"] for f in filas if f["ciclo"] == "2"] == ["BLOQUEO"] * 3
    assert open(out, encoding="utf-8").read().count("hora,ciclo") == 1               # un solo encabezado


@needs_curl
def test_loop_runs_multiple_cycles_and_stops(base, tmp_path):
    out = tmp_path / "loop.csv"
    assert probe.main(["--url", base + "/ok", "--out", str(out), "--loop", "--every", "0.5", "--pausa", "0", "--hours", "0.0008"]) == 0
    assert len(list(csv.DictReader(open(out, encoding="utf-8")))) >= 6              # ≥ 2 ciclos de 3 pedidos


def test_summary_reports_block_lifted_and_ongoing(tmp_path):
    t0 = datetime(2026, 9, 20, 1, 0)
    filas = []
    for i, (h, c) in enumerate([("OK", "OK"), ("BLOQUEO", "OK"), ("BLOQUEO", "BLOQUEO"), ("OK", "BLOQUEO"), ("BLOQUEO", "BLOQUEO")]):
        for m, r in (("httpx_h1", h), ("curl", c), ("curl_simple", c)):
            filas.append({"hora": (t0 + timedelta(hours=i)).isoformat(), "ciclo": i + 1, "metodo": m, "resultado": r,
                          "codigo": "", "http": "", "cf_mitigated": "", "avisos": "", "bytes": "", "ms": "", "error": ""})
    p = tmp_path / "s.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=probe.CAMPOS)
        w.writeheader()
        w.writerows(filas)
    txt = probe.resumen(p)
    assert "15 pedidos" in txt and "[httpx_h1] 5 pedidos: OK=2" in txt and "[curl] 5 pedidos: OK=2" in txt
    assert "·XX·X" in txt and "bloqueo levantado: bloqueado desde 20/09 02:00, ok de nuevo a las 20/09 04:00 (2.0 h)" in txt
    assert "bloqueo EN CURSO desde 20/09 05:00" in txt and "distinto resultado: 2 de 5" in txt and "bloqueo EN CURSO desde 20/09 03:00" in txt


@needs_curl
def test_curl_simple_sends_only_user_agent_while_scraper_curl_sends_its_extras(base):
    SEEN.clear()
    probe.medir("curl_simple", base + "/ok")
    h = SEEN[-1]
    assert h["user-agent"].startswith("inmo-scrapper/") and "accept-language" not in h and "accept-encoding" not in h
    probe.medir("curl", base + "/ok")
    h = SEEN[-1]
    assert h["user-agent"].startswith("inmo-scrapper/") and "accept-language" in h and "accept-encoding" in h
