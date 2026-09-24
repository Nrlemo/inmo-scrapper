"""Conector MercadoLibre Inmuebles (listado web: la API oficial no trae resultados). Selectores verificados sobre HTML
real (tests/fixtures/mercadolibre_listado.html, recortado: sin el header con los datos de la cuenta).

robots.txt (2026-09, grupo `User-agent: *`) prohíbe, entre otros, los filtros `_PriceRange_`, `_PriceMin_`,
`_PriceMax_`, `_TOTAL*AREA_`, `_COVERED*AREA_`, `_FULL*BATHROOMS_`, `_PARKING*LOTS_`, `_Banos_` y `_Cocheras_`:
precio y superficie no pueden ir en la URL y se filtran al recibir la página (matches_profile). La tarjeta no trae
expensas, coordenadas ni inmobiliaria, y sólo una foto.

Paginación: **sin verificar** (la página de prueba tenía 34 resultados, menos de una página). Se usa el formato
habitual del sitio (`_Desde_49`, 48 avisos por página) hasta confirmarlo con una URL real de la página 2.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser, Node

from .base import Listing, Portal
from .common import _clean, _num

BASE = "https://inmuebles.mercadolibre.com.ar"
PAGE_SIZE = 48               # sin verificar (ver docstring)
MAX_PAGES = 5                # tope propio: robots.txt no limita la paginación, pero una ronda cortés tampoco la necesita
_PROHIBIDOS = ("_PriceRange_", "_PriceMin_", "_PriceMax_", "_TOTAL*AREA_", "_TOTAL%2AAREA_", "_COVERED*AREA_",
               "_COVERED%2AAREA_", "_FULL*BATHROOMS_", "_PARKING*LOTS_", "_Banos_", "_Cocheras_", "_OrderId_",
               "_DisplayType_", "_PublishedToday_", "_ItemTypeID_", "_RealEstateAgency_", "_CustId_", "_Discount_")
_ID_RE = re.compile(r"/MLA-?(\d+)")


def _text(node: Node, css: str) -> str | None:
    n = node.css_first(css)
    return _clean(n.text(separator=" ")) if n else None


def parse_total(doc: HTMLParser) -> int | None:
    """'34 resultados' -> 34 ; '1.234 resultados' -> 1234."""
    n = doc.css_first(".ui-search-search-result__quantity-results")
    m = re.search(r"([\d.]+)\s+resultado", n.text()) if n else None
    return int(m.group(1).replace(".", "")) if m else None


def parse_card(item: Node) -> Listing | None:
    a = item.css_first("a.poly-component__title")
    if a is None or not a.attributes.get("href"):
        return None
    url = a.attributes["href"].split("#", 1)[0]
    campo = item.css_first('input[name="id"]')
    id_ = campo.attributes.get("value") if campo else None
    if not id_ and (m := _ID_RE.search(url)):
        id_ = f"MLA{m.group(1)}"
    if not id_:
        return None
    simbolo = _text(item, ".poly-price__current .andes-money-amount__currency-symbol")
    fraccion = _text(item, ".poly-price__current .andes-money-amount__fraction")
    precio = _num(fraccion) if fraccion else None
    moneda = {"US$": "USD", "U$S": "USD", "$": "ARS"}.get(simbolo or "")
    feats: dict[str, float] = {}
    for li in item.css("li.poly-attributes_list__item"):
        t = _clean(li.text()) or ""
        m = re.match(r"([\d.,]+)\s*(ambs?|dorms?|baños?|m²\s*cubiertos|m²\s*totales)", t)
        if m and (v := _num(m.group(1))) is not None:
            clave = m.group(2)
            feats[{"a": "amb", "d": "dorm", "b": "baño"}.get(clave[0], re.sub(r"\s+", "", clave))] = v
    direccion = barrio = None
    if loc := _text(item, ".poly-component__location"):
        partes = [p.strip() for p in loc.split(",") if p.strip()]
        direccion = re.sub(r"\s+", " ", partes[0]) if len(partes) >= 2 else None
        barrio = partes[-2] if len(partes) >= 3 else (partes[-1] if partes else None)
    img = item.css_first("img.poly-component__picture")
    foto = img.attributes.get("src") if img else None
    i = lambda k: int(feats[k]) if k in feats else None  # noqa: E731
    return Listing(
        portal="mercadolibre", id_externo=id_, url=url, titulo=_clean(a.text()), direccion=direccion, barrio=barrio,
        precio=precio, moneda=moneda, ambientes=i("amb"), dormitorios=i("dorm"), banos=i("baño"),
        m2_cubiertos=feats.get("m²cubiertos"), m2_totales=feats.get("m²totales"),
        fotos=[foto] if foto and foto.startswith("http") else [],
    )


def parse_listing_page(text: str) -> tuple[list[Listing], int | None]:
    doc = HTMLParser(text)
    out, vistos = [], set()
    for item in doc.css("li.ui-search-layout__item"):
        l = parse_card(item)
        if l and l.id_externo not in vistos:
            vistos.add(l.id_externo)
            out.append(l)
    return out, parse_total(doc)


def page_url(search_url: str, n: int) -> str:
    """Página n: `…/_Desde_49` (48 por página). SIN VERIFICAR: ver el docstring del módulo."""
    if n <= 1:
        return search_url
    base = search_url.split("#", 1)[0].rstrip("/")
    return f"{base}/_Desde_{(n - 1) * PAGE_SIZE + 1}"


def problema_robots(url: str) -> str | None:
    partes = urlsplit(url)
    ruta = partes.path + (("?" + partes.query) if partes.query else "")
    for p in _PROHIBIDOS:
        if p in ruta:
            return (f"MercadoLibre: robots.txt no permite el filtro «{p.strip('_')}» en la URL; el precio y la "
                    "superficie se filtran al recibir la página")
    return None


class MercadoLibre(Portal):
    nombre, etiqueta = "mercadolibre", "MercadoLibre"
    host = BASE + "/"
    page_size, max_pages = PAGE_SIZE, MAX_PAGES

    def page_url(self, url: str, n: int) -> str:
        return page_url(url, n)

    def parse_page(self, html: str) -> tuple[list[Listing], int | None]:
        return parse_listing_page(html)

    def problema_url(self, url: str) -> str | None:
        return problema_robots(url)

    def miniatura(self, foto: str) -> str:
        """Del srcset real: `D_NQ_NP_2X_…-E.webp` es la de 560 px; `D_NQ_NP_…-V.webp`, la de 320 px."""
        if "mlstatic.com/" not in foto:
            return foto
        return re.sub(r"-[A-Z]\.(webp|jpg)$", r"-V.\1", foto.replace("D_NQ_NP_2X_", "D_NQ_NP_"))


PORTAL = MercadoLibre()
