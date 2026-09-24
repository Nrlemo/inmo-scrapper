"""Conector Zonaprop. Selectores verificados sobre HTML real (tests/fixtures/zonaprop_listado.html).

Notas de robots.txt: solo se permiten las páginas 2..5 (`-pagina-N.html`) => máx. 5 páginas por búsqueda.
El listado solo informa m² totales; los cubiertos se toman de la descripción si figuran ahí.
"""
from __future__ import annotations

import html
import logging
import math
import re
from bisect import bisect_right
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from ..http import PoliteClient
from .common import _clean, _money, _num, matches_profile  # noqa: F401 (re-exportados)
from .base import BlockedError, Connector, Listing, SearchResult

log = logging.getLogger(__name__)

BASE = "https://www.zonaprop.com.ar"
PAGE_SIZE = 30
ROBOTS_MAX_PAGES = 5


def _text(node: Node, css: str) -> str | None:
    n = node.css_first(css)
    return _clean(n.text(separator=" ")) if n else None


_LOGO_RE = re.compile(r"/empresas/((?:\d+/)+?)\d+x\d+/logo_(.+?)(?:_\d+)?\.\w+(?:\?.*)?$")
_CORREDOR_RE = re.compile(
    r"Corredor(?:a)? (?:Responsable|Inmobiliari[oa]):\s*(.+?)(?:\s*-?\s*Contacto:|Nota importante|\.\s|$)", re.I)


def parse_publisher(logo_url: str | None) -> tuple[str | None, str | None]:
    """Logo '.../empresas/1/00/17/60/77/09/130x70/logo_remax-premium-ii_1769536932014.jpg'
    -> ('10017607709', 'remax premium ii'). El id es estable; el nombre es aproximado (slug del archivo)."""
    m = _LOGO_RE.search(logo_url or "")
    if not m:
        return None, None
    return m.group(1).replace("/", ""), m.group(2).replace("-", " ")


def parse_corredor(desc: str | None) -> str | None:
    m = _CORREDOR_RE.search(desc or "")
    return m.group(1).strip()[:200] if m else None


def parse_total(doc: HTMLParser) -> int | None:
    """'2.864 Departamentos en venta ...' (título de la página) -> 2864."""
    t = doc.css_first("title")
    m = re.match(r"\s*([\d.]+)\s", t.text()) if t else None
    return int(m.group(1).replace(".", "")) if m else None


def parse_card(card: Node) -> Listing | None:
    id_ = card.attributes.get("data-id")
    path = card.attributes.get("data-to-posting")
    if not id_ or not path:
        return None
    precio, moneda = _money(_text(card, '[data-qa="POSTING_CARD_PRICE"]'))
    expensas, mon_exp = _money(_text(card, '[data-qa="expensas"]'))

    feats: dict[str, float] = {}
    for span in card.css('[data-qa="POSTING_CARD_FEATURES"] span'):
        t = (span.text() or "").strip()
        m = re.match(r"([\d.,]+)\s*(m²\s*tot|m²\s*cub|amb|dorm|baño|coch)", t)
        if m and (v := _num(m.group(1))) is not None:
            feats[m.group(2).replace(" ", "")] = v

    desc = _text(card, '[data-qa="POSTING_CARD_DESCRIPTION"]')
    cubiertos = feats.get("m²cub")
    if cubiertos is None and desc:
        m = re.search(r"superficie cubierta:?\s*([\d.,]+(?:\s*,\s*\d+)?)\s*m", desc, re.I)
        if m:
            cubiertos = _num(re.sub(r"\s+", "", m.group(1)))

    loc = _text(card, '[data-qa="POSTING_CARD_LOCATION"]')
    img = card.css_first('[data-qa="POSTING_CARD_GALLERY"] img')  # el 2º <img> del card es el logo de la inmobiliaria
    foto = (img.attributes.get("src") or img.attributes.get("data-src")) if img else None
    logo_el = card.css_first('[data-qa="POSTING_CARD_PUBLISHER"]')
    logo = logo_el.attributes.get("src") if logo_el else None
    emp_id, emp_nombre = parse_publisher(logo)
    i = lambda k: int(feats[k]) if k in feats else None  # noqa: E731

    return Listing(
        portal="zonaprop",
        id_externo=id_,
        url=urljoin(BASE, path),
        titulo=_clean(img.attributes.get("alt")) if img else None,
        direccion=_text(card, '[class*="location-address"]'),
        barrio=loc.split(",")[0].strip() if loc else None,
        precio=precio, moneda=moneda, expensas=expensas, moneda_expensas=mon_exp,
        ambientes=i("amb"), dormitorios=i("dorm"), banos=i("baño"), cocheras=i("coch"),
        m2_cubiertos=cubiertos, m2_totales=feats.get("m²tot"),
        descripcion=desc,
        fotos=[foto] if foto and foto.startswith("http") else [],
        inmobiliaria_id_externo=emp_id, inmobiliaria_nombre=emp_nombre,
        inmobiliaria_logo=logo if emp_id else None, corredor=parse_corredor(desc),
    )


def parse_geolocations(text: str) -> dict[str, tuple[float, float]]:
    """id de aviso -> (lat, lng), del estado precargado (__PRELOADED_STATE__) de la página de listado."""
    ids = [(m.start(), m.group(1)) for m in re.finditer(r'"postingId":"(\d+)"', text)]
    pos = [p for p, _ in ids]
    out: dict[str, tuple[float, float]] = {}
    for m in re.finditer(r'"geolocation":\{"latitude":(-?[\d.]+),"longitude":(-?[\d.]+)\}', text):
        k = bisect_right(pos, m.start()) - 1
        if k >= 0:
            out.setdefault(ids[k][1], (float(m.group(1)), float(m.group(2))))
    return out


MAX_FOTOS = 20


def parse_pictures(text: str) -> dict[str, list[str]]:
    """id de aviso -> URLs de sus fotos (720x532), del estado precargado de la página de listado.

    El HTML de cada tarjeta trae sólo la primera foto; el estado precargado trae todas las visibles del aviso
    (visiblePictures), así que la galería no requiere ningún pedido extra al portal.
    """
    ids = [(m.start(), m.group(1)) for m in re.finditer(r'"postingId":"(\d+)"', text)]
    pos = [p for p, _ in ids]
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'"url730x532":"(https?:[^"]+)"', text):
        k = bisect_right(pos, m.start()) - 1
        if k >= 0:
            fotos = out.setdefault(ids[k][1], [])
            url = m.group(1).replace("\\u002F", "/").replace("\\/", "/")
            if url not in fotos and len(fotos) < MAX_FOTOS:
                fotos.append(url)
    return out


def parse_listing_page(text: str) -> tuple[list[Listing], int | None]:
    """Devuelve (avisos individuales, total de resultados). Emprendimientos (DEVELOPMENT) se omiten."""
    doc = HTMLParser(text)
    out, seen = [], set()
    geo = parse_geolocations(text)
    fotos = parse_pictures(text)
    for card in doc.css("[data-posting-type][data-id]"):
        if card.attributes["data-posting-type"] != "PROPERTY":
            continue
        listing = parse_card(card)
        if listing and listing.id_externo not in seen:
            seen.add(listing.id_externo)
            listing.lat, listing.lng = geo.get(listing.id_externo, (None, None))
            listing.fotos = fotos.get(listing.id_externo) or listing.fotos
            out.append(listing)
    return out, parse_total(doc)


def page_url(search_url: str, n: int) -> str:
    """https://.../x.html -> https://.../x-pagina-N.html (n=1 devuelve la URL original)."""
    if n <= 1:
        return search_url
    return re.sub(r"\.html$", f"-pagina-{n}.html", search_url)


def search_urls(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """[(etiqueta, url)]. Config: `search_urls` (lista explícita) o `zones` + `search_url_template`.

    Cada zona es un string ("almagro") o un dict con overrides ({zone: almagro, price_min: 50000, price_max: 110000}),
    para partir una zona con más de 150 resultados. Placeholders de la plantilla: {zone}, {price_min}, {price_max}
    (por defecto, el rango de precio del perfil).
    """
    cfg = profile["portals"]["zonaprop"]
    if cfg.get("zones"):
        tpl = cfg["search_url_template"]
        price = profile.get("price", {})
        out = []
        for z in cfg["zones"]:
            params = {"price_min": price.get("min"), "price_max": price.get("max")}
            params.update(z if isinstance(z, dict) else {"zone": z})
            label = params["zone"] if not isinstance(z, dict) or len(z) == 1 else \
                f"{params['zone']} {params['price_min']}-{params['price_max']}"
            out.append((label, tpl.format(**params)))
        return out
    urls = cfg.get("search_urls") or [cfg["search_url"]]
    return [(re.sub(r"^.*/|\.html$", "", u)[:40], u) for u in urls]


class ZonapropConnector(Connector):
    portal = "zonaprop"

    def __init__(self, politeness: dict[str, Any], client: PoliteClient | None = None):
        self.cfg = politeness
        self.client = client or PoliteClient(
            delay=tuple(politeness["page_delay"]), retries=politeness.get("retries", 1),
            backoff=tuple(politeness.get("backoff", (120, 600))), http_client=politeness.get("http_client"),
        )

    def search(self, profile: dict[str, Any]) -> SearchResult:
        res = SearchResult()
        urls = search_urls(profile)
        zone_delay = tuple(self.cfg.get("zone_delay", self.cfg["page_delay"]))
        seen: set[str] = set()
        self._notify(zonas_total=len(urls), zona_idx=0)
        for k, (zone, url) in enumerate(urls):
            self._notify(zona_idx=k + 1, zona_actual=zone, pagina=0, paginas=0)
            try:
                self._search_url(zone, url, profile, res, seen, zone_delay if k else None)
            except BlockedError as e:
                res.bloqueada, res.completa = True, False
                res.errores.append(f"[{zone}] {e}")
                break  # mismo sitio: si bloquea una zona, no se insiste con las demás
            except Exception as e:  # noqa: BLE001 - una zona que falla no debe tumbar a las demás
                log.exception("zonaprop [%s] falló", zone)
                res.completa = False
                res.errores.append(f"[{zone}] {type(e).__name__}: {e}")
        return res

    def _search_url(self, zone: str, url: str, profile: dict[str, Any], res: SearchResult,
                    seen: set[str], first_delay: tuple[float, float] | None) -> None:
        max_pages = min(self.cfg.get("max_pages", ROBOTS_MAX_PAGES), ROBOTS_MAX_PAGES)
        pages, n, zone_seen = max_pages, 1, set()
        while n <= pages:
            # Entre zonas se usa una pausa más larga (zone_delay) para la 1ª página de la zona.
            kw = {"delay": first_delay} if n == 1 and first_delay else {}
            page, total = parse_listing_page(self.client.get(page_url(url, n), **kw))
            if n == 1 and total is not None:
                pages = min(math.ceil(total / PAGE_SIZE), max_pages)
                log.info("zonaprop [%s]: %s resultados, %s página(s) a recorrer", zone, total, pages)
                if total > pages * PAGE_SIZE:
                    res.truncada = True
                    res.errores.append(
                        f"[{zone}] {total} resultados pero solo se pueden leer {pages * PAGE_SIZE} "
                        "(robots.txt/max_pages): dividir esta zona en búsquedas más chicas")
            self._notify(pagina=n, paginas=pages)
            new = [l for l in page if l.id_externo not in zone_seen]
            if not new:
                log.info("zonaprop [%s]: página %d sin avisos nuevos, corto", zone, n)
                break
            zone_seen.update(l.id_externo for l in new)
            for l in new:
                if l.id_externo not in seen and matches_profile(l, profile):
                    seen.add(l.id_externo)  # una zona puede solaparse con otra (ej. Almagro / Almagro Norte)
                    res.listings.append(l)
            self._notify(resultados=len(res.listings))
            n += 1
