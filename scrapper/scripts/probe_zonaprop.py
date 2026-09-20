"""Reconocimiento de Zonaprop: 1 robots.txt + 1 página de listado. Solo guarda y reporta.

Uso: python scripts/probe_zonaprop.py [URL_DE_BUSQUEDA]
"""
import re
import sys
import time
from pathlib import Path

import httpx

from inmo.http import UA  # el contacto sale de INMO_CONTACT

BASE = "https://www.zonaprop.com.ar"
DEFAULT_URL = f"{BASE}/departamentos-venta-villa-urquiza.html"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw"

MARKERS = {
    "cloudflare": r"cloudflare|cf-chl|challenge-platform|just a moment",
    "captcha": r"captcha|hcaptcha|recaptcha",
    "next_data": r"__NEXT_DATA__",
    "json_ld": r"application/ld\+json",
    "data_posting": r"data-posting-type|data-qa=\"posting",
    "dataLayer": r"dataLayer",
}


def save(name: str, content: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(content, encoding="utf-8")


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    headers = {"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30) as c:
        r = c.get(f"{BASE}/robots.txt")
        print(f"robots.txt -> {r.status_code}")
        save("zonaprop_robots.txt", r.text)
        print(r.text[:1500])

        time.sleep(8)
        r = c.get(url)
        print(f"\n{url} -> {r.status_code} ({len(r.text)} bytes)")
        for h in ("server", "cf-ray", "cf-mitigated", "content-type"):
            if h in r.headers:
                print(f"  {h}: {r.headers[h]}")
        save("zonaprop_listado.html", r.text)
        for name, pat in MARKERS.items():
            print(f"  {name}: {len(re.findall(pat, r.text, re.I))} coincidencias")
    print(f"\nGuardado en {OUT}")


if __name__ == "__main__":
    main()
