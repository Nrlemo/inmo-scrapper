"""Medición de bajo volumen: ¿qué cliente HTTP pasa la protección de Zonaprop, y cuándo se levantan los bloqueos?

Cada "ciclo" hace exactamente 3 pedidos a la MISMA página de listado (uno por cliente, en orden aleatorio, con una
pausa entre ellos) y anota el resultado en un CSV. Los clientes:
  - httpx_h1    : httpx sobre HTTP/1.1 (el cliente por defecto del scrapper)
  - curl        : el binario curl tal como lo usa el scrapper (INMO_HTTP_CLIENT=curl): --compressed, -L, Accept-Language
  - curl_simple : curl mínimo, sólo User-Agent y URL (la forma que pasó en las pruebas manuales). Comparado con `curl`
                  muestra si los argumentos extra del scrapper influyen en la decisión del sitio

Uso
  python probe_metodos.py                                   # un ciclo (3 pedidos); ideal para cron
  python probe_metodos.py --loop --every 3600 --hours 72    # un ciclo por hora durante 72 h
  python probe_metodos.py --resumen medicion_zonaprop.csv   # resumen de lo medido (no hace pedidos)

Límites de cortesía (no se pueden bajar contra un sitio remoto): un ciclo cada 30 min como mínimo y 20 s entre
pedidos. El intervalo lleva una variación aleatoria de ±10 % para no ser estrictamente periódico.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# `inmo` puede estar instalado, en INMO_SRC (imagen Docker: /srv/scrapper_src) o en ../src (repo).
for _p in (os.environ.get("INMO_SRC"), "/srv/scrapper_src", str(Path(__file__).resolve().parent.parent / "src")):
    if _p and Path(_p).is_dir() and _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402

from inmo.http import UA, _CurlClient  # noqa: E402

URL_DEFECTO = ("https://www.zonaprop.com.ar/departamentos-venta-barrio-norte-con-apto-credito-mas-de-2-"
               "habitaciones-mas-de-3-ambientes-50000-125000-dolar.html")
METODOS = ("httpx_h1", "curl", "curl_simple")
CAMPOS = ["hora", "ciclo", "metodo", "resultado", "codigo", "http", "cf_mitigated", "avisos", "bytes", "ms", "error"]
MIN_EVERY, MIN_PAUSA = 1800, 20
CHALLENGE = re.compile(r"<title[^>]*>\s*(just a moment|attention required|un momento)", re.I)
_stop = False


def es_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def validar_limites(url: str, every: float, pausa: float) -> str | None:
    """Mensaje de error si los parámetros serían descorteses con un sitio remoto."""
    if not url.startswith(("https://", "http://")):
        return "La URL debe empezar con http:// o https://"
    if not es_local(url):
        if every < MIN_EVERY:
            return f"--every mínimo {MIN_EVERY} s (un ciclo cada 30 min) contra un sitio remoto"
        if pausa < MIN_PAUSA:
            return f"--pausa mínimo {MIN_PAUSA} s entre pedidos"
    return None


def clasificar(codigo: int | str, cuerpo: str, cf: str | None) -> str:
    """OK | BLOQUEO | ANOMALO (200 sin avisos ni título de desafío) | ERROR."""
    if codigo in (403, 429) or (cf or "").lower() == "challenge" or CHALLENGE.search(cuerpo[:20000]):
        return "BLOQUEO"
    if codigo == 200:
        return "OK" if cuerpo.count('data-posting-type="PROPERTY"') > 0 else "ANOMALO"
    return "ERROR"


def _curl_simple(url: str, timeout: float) -> tuple[int, str]:
    """curl mínimo: sólo User-Agent y URL (sin --compressed, sin -L, sin Accept-Language)."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("el binario curl no está instalado")
    p = subprocess.run([curl, "-sS", "--max-time", str(int(timeout)), "-A", UA, "-w", "\n%{http_code}", "--url", url],
                       capture_output=True, timeout=timeout + 10, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"curl salió con código {p.returncode}: {p.stderr.decode('utf-8', 'replace').strip()[:150]}")
    cuerpo, _, codigo = p.stdout.rpartition(b"\n")
    return int(codigo.strip() or 0), cuerpo.decode("utf-8", "replace")


def medir(metodo: str, url: str, timeout: float = 30) -> dict:
    t0 = time.monotonic()
    fila = {"metodo": metodo, "codigo": "", "http": "", "cf_mitigated": "", "avisos": "", "bytes": "", "error": ""}
    try:
        if metodo == "httpx_h1":
            with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"},
                              follow_redirects=True, timeout=timeout) as c:
                r = c.get(url)
            cuerpo, fila["codigo"], fila["http"] = r.text, r.status_code, r.http_version
            fila["cf_mitigated"] = r.headers.get("cf-mitigated") or ""
        elif metodo == "curl":
            r = _CurlClient(timeout).get(url)
            cuerpo, fila["codigo"], fila["http"] = r.text, r.status_code, "curl"
        else:  # curl_simple
            fila["codigo"], cuerpo = _curl_simple(url, timeout)
            fila["http"] = "curl"
        fila["bytes"], fila["avisos"] = len(cuerpo), cuerpo.count('data-posting-type="PROPERTY"')
        fila["resultado"] = clasificar(fila["codigo"], cuerpo, fila["cf_mitigated"])
    except Exception as e:  # noqa: BLE001 - un fallo de red se registra, no aborta la medición
        fila["resultado"], fila["codigo"], fila["error"] = "ERROR", "ERROR", f"{type(e).__name__}: {e}"[:200]
    fila["ms"] = int((time.monotonic() - t0) * 1000)
    return fila


def ciclo(url: str, numero: int, out: Path, pausa: float, sleep=time.sleep) -> list[dict]:
    orden = list(METODOS)
    random.shuffle(orden)                       # sin sesgo de orden
    filas = []
    for i, m in enumerate(orden):
        if i:
            sleep(pausa)
        f = medir(m, url)
        f.update(hora=datetime.now().astimezone().isoformat(timespec="seconds"), ciclo=numero)
        filas.append(f)
        nuevo = not out.exists() or out.stat().st_size == 0
        with open(out, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CAMPOS)
            if nuevo:
                w.writeheader()
            w.writerow({k: f.get(k, "") for k in CAMPOS})
        print(f"{f['hora']} ciclo {numero:>3} {m:<11} {f['resultado']:<8} código={f['codigo']} http={f['http']} "
              f"avisos={f['avisos']} {f['ms']} ms {f['error']}", flush=True)
    return filas


def resumen(ruta: Path) -> str:
    with open(ruta, newline="", encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    if not filas:
        return "Sin datos."
    out = [f"{len(filas)} pedidos, {filas[0]['hora']} → {filas[-1]['hora']}"]
    for m in METODOS:
        fs = [f for f in filas if f["metodo"] == m]
        if not fs:
            continue
        cuenta = {k: sum(1 for f in fs if f["resultado"] == k) for k in ("OK", "BLOQUEO", "ANOMALO", "ERROR")}
        pct = 100 * cuenta["OK"] / len(fs)
        out.append(f"\n[{m}] {len(fs)} pedidos: OK={cuenta['OK']} ({pct:.0f} %) BLOQUEO={cuenta['BLOQUEO']} "
                   f"ANOMALO={cuenta['ANOMALO']} ERROR={cuenta['ERROR']}")
        out.append("  línea de tiempo: " + "".join({"OK": "·", "BLOQUEO": "X", "ANOMALO": "?", "ERROR": "e"}[f["resultado"]] for f in fs)
                   + "   (· ok, X bloqueo, ? anómalo, e error)")
        # rachas de bloqueo: desde el primer bloqueo hasta el primer OK que le sigue
        inicio = None
        for f in fs:
            t = datetime.fromisoformat(f["hora"])
            if f["resultado"] == "BLOQUEO" and inicio is None:
                inicio = t
            elif f["resultado"] == "OK" and inicio is not None:
                out.append(f"  bloqueo levantado: bloqueado desde {inicio:%d/%m %H:%M}, ok de nuevo a las {t:%d/%m %H:%M} "
                           f"({(t - inicio).total_seconds() / 3600:.1f} h)")
                inicio = None
        if inicio is not None:
            out.append(f"  bloqueo EN CURSO desde {inicio:%d/%m %H:%M}")
    por_ciclo: dict[str, dict[str, str]] = {}
    for f in filas:
        por_ciclo.setdefault(f["ciclo"], {})[f["metodo"]] = f["resultado"]
    dif = [(c, r) for c, r in por_ciclo.items() if len(r) == len(METODOS) and len(set(r.values())) > 1]
    out.append(f"\nCiclos en que los clientes dieron distinto resultado: {len(dif)} de {len(por_ciclo)}")
    for c, r in dif[:10]:
        out.append(f"  ciclo {c}: " + ", ".join(f"{m}={v}" for m, v in r.items()))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=URL_DEFECTO, help="siempre la misma página, para comparar (default: Barrio Norte)")
    ap.add_argument("--out", default="medicion_zonaprop.csv")
    ap.add_argument("--loop", action="store_true", help="repetir ciclos hasta cumplir --hours")
    ap.add_argument("--every", type=float, default=3600, help="segundos entre ciclos (mínimo 1800 contra un sitio remoto)")
    ap.add_argument("--hours", type=float, default=72, help="duración total del modo --loop")
    ap.add_argument("--pausa", type=float, default=30, help="segundos entre pedidos de un ciclo (mínimo 20)")
    ap.add_argument("--resumen", metavar="CSV", help="solo mostrar el resumen de un CSV existente")
    a = ap.parse_args(argv)
    if a.resumen:
        print(resumen(Path(a.resumen)))
        return 0
    if (err := validar_limites(a.url, a.every, a.pausa)):
        print("Error:", err, file=sys.stderr)
        return 2
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def parar(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, parar)
    signal.signal(signal.SIGINT, parar)

    fin = time.monotonic() + a.hours * 3600
    n = 0
    while True:
        n += 1
        ciclo(a.url, n, out, a.pausa)
        if not a.loop or _stop or time.monotonic() + a.every * 0.9 >= fin:
            break
        espera = a.every * random.uniform(0.9, 1.1)
        t_end = time.monotonic() + espera
        while time.monotonic() < t_end and not _stop:
            time.sleep(min(5, t_end - time.monotonic()))
        if _stop:
            break
    print(f"\nListo: {n} ciclo(s). Resumen:  python {Path(__file__).name} --resumen {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
