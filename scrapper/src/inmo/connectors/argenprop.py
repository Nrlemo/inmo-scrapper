"""Conector Argenprop. Selectores verificados sobre HTML real (tests/fixtures/argenprop_listado.html).

robots.txt (2026-09): sólo se permiten las páginas `?pagina-1..3` y siempre que sean la **única** consulta de la URL
(`Allow: /*?pagina-2$`, `Disallow: /*?pagina-`) => máx. 3 páginas (60 avisos) por búsqueda y los filtros van en la ruta,
no en `?...`. Además `Disallow: /*-o-*-o-*`: una URL con dos o más «-o-» (p. ej. `casas-o-departamentos-o-ph`) no se
puede pedir. El listado no trae coordenadas.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from .base import Listing, Portal
from .common import _clean, _money, _num

BASE = "https://www.argenprop.com"
PAGE_SIZE = 20
ROBOTS_MAX_PAGES = 3
MAX_FOTOS = 20


def _text(node: Node, css: str) -> str | None:
    n = node.css_first(css)
    return _clean(n.text(separator=" ")) if n else None


def parse_total(doc: HTMLParser) -> int | None:
    """'433 Casas, departamentos y ph con …' (título de la página) -> 433."""
    t = doc.css_first("title")
    m = re.match(r"\s*([\d.]+)\s", t.text()) if t else None
    return int(m.group(1).replace(".", "")) if m else None


def parse_card(item: Node) -> Listing | None:
    a = item.css_first("a.card")
    if a is None:
        return None
    id_ = a.attributes.get("idaviso") or item.attributes.get("id")
    href = a.attributes.get("href")
    if not id_ or not href:
        return None
    precio, moneda = _money(_text(item, "p.card__price"))
    expensas, mon_exp = _money(_text(item, "p.card__expenses"))
    feats: dict[str, float] = {}
    for li in item.css("ul.card__main-features li"):
        t = _clean(li.text(separator=" ")) or ""
        m = re.match(r"([\d.,]+)\s*(m²\s*cubie|m²\s*tot|dorm|baño|ambiente|amb)", t)
        if m and (v := _num(m.group(1))) is not None:
            feats[re.sub(r"\s+", "", m.group(2)).replace("ambiente", "amb")] = v
    direccion = barrio = None
    if addr := _text(item, "p.card__address"):
        direccion, _, barrio = addr.rpartition(",")
        direccion, barrio = (direccion.strip() or None, barrio.strip() or None) if direccion else (addr, None)
    fotos = []
    for img in item.css("ul.card__photos img"):
        src = img.attributes.get("src") or img.attributes.get("data-src")
        if src and src.startswith("http") and src not in fotos and len(fotos) < MAX_FOTOS:
            fotos.append(src)
    logo = item.css_first(".card__agent img")
    i = lambda k: int(feats[k]) if k in feats else None  # noqa: E731
    return Listing(
        portal="argenprop", id_externo=str(id_), url=urljoin(BASE, href),
        titulo=_text(item, ".card__title"), direccion=direccion, barrio=barrio,
        precio=precio, moneda=moneda, expensas=expensas, moneda_expensas=mon_exp,
        ambientes=i("amb"), dormitorios=i("dorm"), banos=i("baño"),
        m2_cubiertos=feats.get("m²cubie"), m2_totales=feats.get("m²tot"),
        descripcion=_text(item, ".card__info"), fotos=fotos,
        inmobiliaria_id_externo=a.attributes.get("idanunciante") or None,
        inmobiliaria_nombre=_text(item, ".card__agent-name"),
        inmobiliaria_logo=(logo.attributes.get("data-src") or logo.attributes.get("src")) if logo else None,
    )


def parse_listing_page(text: str) -> tuple[list[Listing], int | None]:
    doc = HTMLParser(text)
    out, vistos = [], set()
    for item in doc.css("div.listing__item"):
        l = parse_card(item)
        if l and l.id_externo not in vistos:
            vistos.add(l.id_externo)
            out.append(l)
    return out, parse_total(doc)


def page_url(search_url: str, n: int) -> str:
    """Formato verificado en el portal: `…?pagina-2`. robots.txt sólo permite `?pagina-N` como única consulta."""
    return search_url if n <= 1 else f"{search_url.split('?', 1)[0]}?pagina-{n}"


def problema_robots(url: str) -> str | None:
    """Por qué robots.txt no permite pedir esta búsqueda (None si se puede)."""
    partes = urlsplit(url.replace("{zone}", "zona").replace("{price_min}", "0").replace("{price_max}", "0"))
    if partes.query:
        return ("Argenprop: robots.txt no permite filtros en la consulta (?…) junto con la paginación; "
                "usá sólo filtros que vayan en la ruta")
    if (partes.path + partes.query).count("-o-") >= 2:
        return "Argenprop: robots.txt no permite URLs con dos o más «-o-» (p. ej. casas-o-departamentos-o-ph)"
    return None


class Argenprop(Portal):
    nombre, etiqueta = "argenprop", "Argenprop"
    host = BASE + "/"
    page_size, max_pages = PAGE_SIZE, ROBOTS_MAX_PAGES

    def page_url(self, url: str, n: int) -> str:
        return page_url(url, n)

    def parse_page(self, html: str) -> tuple[list[Listing], int | None]:
        return parse_listing_page(html)

    def problema_url(self, url: str) -> str | None:
        return problema_robots(url)


PORTAL = Argenprop()

