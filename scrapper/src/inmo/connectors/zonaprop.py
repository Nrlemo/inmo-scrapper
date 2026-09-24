"""Conector Zonaprop. Selectores verificados sobre HTML real (tests/fixtures/zonaprop_listado.html).

Notas de robots.txt: solo se permiten las páginas 2..5 (`-pagina-N.html`) => máx. 5 páginas por búsqueda.
El listado solo informa m² totales; los cubiertos se toman de la descripción si figuran ahí.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from .common import _clean, _money, _num
from .base import Listing, Portal

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


# Segmentos de URL verificados contra búsquedas reales de Zonaprop. Otros tipos, operaciones o monedas no se generan:
# para esos casos está la plantilla avanzada (se arma una búsqueda en el navegador y se pega su URL).
TIPOS = {"departamento": "departamentos"}
OPERACIONES = {"compra": "venta"}
MONEDAS = {"USD": "dolar"}


def armar_plantilla(f: dict[str, Any]) -> str:
    """Filtros -> plantilla de URL de búsqueda con {zone}, {price_min} y {price_max}. Lo que no va en la URL (m²,
    palabras a excluir, máximos) se filtra al recibir la página (matches_profile).

    >>> armar_plantilla({"tipo": "departamento", "operacion": "compra", "moneda": "USD", "apto_credito": True,
    ...                  "dorm_min": 2, "amb_min": 3})
    'https://www.zonaprop.com.ar/departamentos-venta-{zone}-con-apto-credito-mas-de-2-habitaciones-mas-de-3-ambientes-{price_min}-{price_max}-dolar.html'
    """
    for clave, validos in (("tipo", TIPOS), ("operacion", OPERACIONES), ("moneda", MONEDAS)):
        if f.get(clave) not in validos:
            raise ValueError(f"Zonaprop: {clave} «{f.get(clave)}» no está verificado; usá la plantilla avanzada")
    partes = [f"{TIPOS[f['tipo']]}-{OPERACIONES[f['operacion']]}-{{zone}}"]
    if f.get("apto_credito"):
        partes.append("con-apto-credito")
    if f.get("dorm_min"):
        partes.append(f"mas-de-{int(f['dorm_min'])}-habitaciones")
    if f.get("amb_min"):
        partes.append(f"mas-de-{int(f['amb_min'])}-ambientes")
    elif f.get("amb_max"):
        # Verificado: «hasta-1-ambiente». Con mínimo y máximo a la vez no hay un formato verificado: va el mínimo
        # en la URL y el máximo se filtra al recibir la página (matches_profile).
        n = int(f["amb_max"])
        partes.append(f"hasta-{n}-ambiente" + ("s" if n > 1 else ""))
    partes.append(f"{{price_min}}-{{price_max}}-{MONEDAS[f['moneda']]}")
    return f"{BASE}/" + "-".join(partes) + ".html"


_PLANTILLA_RE = re.compile(r"^https://www\.zonaprop\.com\.ar/departamentos-venta-\{zone\}(-con-apto-credito)?"
                           r"(?:-mas-de-(\d+)-habitaciones)?(?:-mas-de-(\d+)-ambientes|-hasta-(\d+)-ambientes?)?"
                           r"-\{price_min\}-\{price_max\}-dolar\.html$")


def leer_plantilla(tpl: str) -> dict[str, Any] | None:
    """Inversa de armar_plantilla (para importar profiles.yaml). None si la plantilla no es una de las que se generan."""
    m = _PLANTILLA_RE.match((tpl or "").strip())
    if not m:
        return None
    return {"tipo": "departamento", "operacion": "compra", "moneda": "USD", "apto_credito": bool(m.group(1)),
            "dorm_min": int(m.group(2)) if m.group(2) else None, "amb_min": int(m.group(3)) if m.group(3) else None,
            "amb_max": int(m.group(4)) if m.group(4) else None}


class Zonaprop(Portal):
    nombre, etiqueta = "zonaprop", "Zonaprop"
    host = BASE + "/"
    page_size, max_pages = PAGE_SIZE, ROBOTS_MAX_PAGES

    def armar_plantilla(self, filtros: dict[str, Any]) -> str:
        return armar_plantilla(filtros)

    def leer_plantilla(self, plantilla: str) -> dict[str, Any] | None:
        return leer_plantilla(plantilla)

    def page_url(self, url: str, n: int) -> str:
        return page_url(url, n)

    def parse_page(self, html: str) -> tuple[list[Listing], int | None]:
        return parse_listing_page(html)

    def miniatura(self, foto: str) -> str:
        """Las fotos se guardan en 720x532 (galería); para listados alcanza la de 360x266 del mismo CDN."""
        return foto.replace("/720x532/", "/360x266/") if "zonapropcdn.com/" in foto else foto


PORTAL = Zonaprop()


def search_urls(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """[(etiqueta, url)] del perfil para Zonaprop (ver Portal.search_urls)."""
    return PORTAL.search_urls(profile)
