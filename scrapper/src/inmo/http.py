"""Cliente HTTP cortés: User-Agent identificable, pausas aleatorias, reintento con backoff, detección de bloqueo."""
from __future__ import annotations

import logging
import random
import re
import time

import httpx

from .errors import BlockedError

log = logging.getLogger(__name__)

UA = "inmo-scrapper/0.1 (uso personal, bajo volumen; contacto: nrlemo@gmail.com)"
CHALLENGE_TITLES = ("just a moment", "attention required", "un momento")


class PoliteClient:
    def __init__(self, delay: tuple[float, float], retries: int = 1,
                 backoff: tuple[float, float] = (120, 600), timeout: float = 30,
                 sleep=time.sleep):
        self.delay, self.retries, self.backoff, self._sleep = delay, retries, backoff, sleep
        self._first = True
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
