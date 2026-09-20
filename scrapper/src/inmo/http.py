"""Cliente HTTP cortés: User-Agent identificable, pausas aleatorias, reintento con backoff, detección de bloqueo."""
from __future__ import annotations

import logging
import os
import random
import re
import shutil
import subprocess
import time

import httpx

from .errors import BlockedError

log = logging.getLogger(__name__)

# Contacto para que el portal pueda avisarte si algo molesta (buena práctica de scraping responsable).
# Se configura con INMO_CONTACT (email o URL); sin definir, el User-Agent no lleva ningún dato personal.
CONTACT = os.environ.get("INMO_CONTACT", "").strip()
UA = "inmo-scrapper/0.1 (uso personal, bajo volumen" + (f"; contacto: {CONTACT}" if CONTACT else "") + ")"
CHALLENGE_TITLES = ("just a moment", "attention required", "un momento")


class CurlError(httpx.TransportError):
    """Fallo de red al usar curl (se reintenta igual que un error de httpx)."""


class _Respuesta:
    """Lo mínimo de httpx.Response que usa PoliteClient."""

    def __init__(self, status_code: int, text: str):
        self.status_code, self.text = status_code, text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _CurlClient:
    """Cliente alternativo que delega el pedido en el binario `curl` (con su User-Agent honesto de siempre).

    Algunos portales con protección anti-bot aceptan a curl en ciertas redes donde rechazan a httpx. No cambia
    la identidad (mismo User-Agent), ni las pausas, ni la detección de bloqueos: sólo el cliente de red.
    """

    def __init__(self, timeout: float):
        self.curl = shutil.which("curl")
        if not self.curl:
            raise RuntimeError("INMO_HTTP_CLIENT=curl pero el binario `curl` no está instalado")
        self.timeout = timeout

    def get(self, url: str) -> _Respuesta:
        if not url.startswith(("https://", "http://")):
            raise ValueError(f"URL no válida: {url!r}")
        cmd = [self.curl, "-sS", "--compressed", "-L", "--max-redirs", "5", "--max-time", str(int(self.timeout)),
               "-A", UA, "-H", "Accept-Language: es-AR,es;q=0.9", "-w", "\n%{http_code}", "--url", url]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=self.timeout + 10, check=False)  # sin shell
        except subprocess.TimeoutExpired as e:
            raise CurlError("curl: tiempo de espera agotado") from e
        if p.returncode != 0:
            raise CurlError(f"curl salió con código {p.returncode}: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
        cuerpo, _, codigo = p.stdout.rpartition(b"\n")
        return _Respuesta(int(codigo.strip() or 0), cuerpo.decode("utf-8", "replace"))

    def close(self) -> None:
        pass


class PoliteClient:
    def __init__(self, delay: tuple[float, float], retries: int = 1,
                 backoff: tuple[float, float] = (120, 600), timeout: float = 30,
                 sleep=time.sleep, http_client: str | None = None):
        self.delay, self.retries, self.backoff, self._sleep = delay, retries, backoff, sleep
        self._first = True
        # Prioridad: variable INMO_HTTP_CLIENT > `http_client` del YAML > httpx
        kind = (os.environ.get("INMO_HTTP_CLIENT") or http_client or "httpx").strip().lower()
        if kind not in ("httpx", "curl"):
            raise ValueError(f"http_client inválido: {kind!r} (usar httpx o curl)")
        log.info("Cliente HTTP: %s", kind)
        if kind == "curl":
            self._client = _CurlClient(timeout)
        else:
            self._client = httpx.Client(
                headers={"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"},
                follow_redirects=True, timeout=timeout,
            )

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, delay: tuple[float, float] | None = None) -> str:
        """GET con pausa previa (salvo el primero). 403/429/challenge -> BlockedError (tras reintentos)."""
        if not self._first:
            self._sleep(random.uniform(*(delay or self.delay)))
        self._first = False
        for attempt in range(self.retries + 1):
            try:
                r = self._client.get(url)
            except httpx.TransportError as e:
                log.warning("Error de red en %s: %s", url, e)
                if attempt == self.retries:
                    raise
            else:
                if r.status_code in (403, 429) or (r.status_code == 503 and self._is_challenge(r.text)):
                    log.warning("Posible bloqueo (%s) en %s", r.status_code, url)
                    if attempt == self.retries:
                        raise BlockedError(f"HTTP {r.status_code} en {url}")
                elif r.status_code == 200:
                    if self._is_challenge(r.text):
                        raise BlockedError(f"Challenge anti-bot en {url}")
                    return r.text
                else:
                    r.raise_for_status()
            wait = random.uniform(*self.backoff) * (attempt + 1)
            log.info("Reintentando en %.0f s", wait)
            self._sleep(wait)
        raise RuntimeError("inalcanzable")

    @staticmethod
    def _is_challenge(text: str) -> bool:
        # Solo se mira el <title>: la página real de Cloudflare-protegidos menciona "challenge" en scripts.
        m = re.search(r"<title[^>]*>(.*?)</title>", text[:20000], re.I | re.S)
        title = m.group(1).strip().lower() if m else ""
        return any(t in title for t in CHALLENGE_TITLES)
