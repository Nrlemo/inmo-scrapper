"""Aviso normalizado (Listing) e interfaz común de los portales (Portal); un módulo por portal en connectors/."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Listing:
    portal: str
    id_externo: str
    url: str
    titulo: str | None = None
    direccion: str | None = None
    barrio: str | None = None
    precio: float | None = None
    moneda: str | None = None
    expensas: float | None = None
    moneda_expensas: str | None = None
    ambientes: int | None = None
    dormitorios: int | None = None
    banos: int | None = None
    cocheras: int | None = None
    m2_cubiertos: float | None = None
    m2_totales: float | None = None
    descripcion: str | None = None
    fotos: list[str] = field(default_factory=list)
    lat: float | None = None
    lng: float | None = None
    inmobiliaria_id_externo: str | None = None
    inmobiliaria_nombre: str | None = None   # aproximado: derivado del nombre del archivo del logo
    inmobiliaria_logo: str | None = None
    corredor: str | None = None              # de la descripción ("Corredor Responsable: ...")


class Portal:
    """Lo que la ronda por navegador necesita de un portal. Una subclase por portal (connectors/<portal>.py), registrada
    en connectors/__init__.py. Fuera de connectors/ nadie nombra un portal concreto: se pasa por el registro."""
    nombre: str                 # clave en filtros, perfiles y publicaciones.portal («zonaprop»)
    etiqueta: str               # para mostrar («Zonaprop»)
    host: str                   # «https://www.zonaprop.com.ar/»: la extensión valida con esto la pestaña que lee
    page_size: int              # avisos por página de resultados
    max_pages: int              # tope de páginas por búsqueda (robots.txt)

    # ---- URLs ----
    def armar_plantilla(self, filtros: dict[str, Any]) -> str:
        """Filtros (inmo.filtros) -> plantilla con {zone}, {price_min} y {price_max}. ValueError si la combinación no
        está verificada para este portal (queda la plantilla avanzada)."""
        raise ValueError(f"{self.etiqueta}: todavía no arma URLs con los filtros; usá la plantilla avanzada")

    def leer_plantilla(self, plantilla: str) -> dict[str, Any] | None:
        """Inversa de armar_plantilla (para importar profiles.yaml). None si no la reconoce."""
        return None

    def page_url(self, url: str, n: int) -> str:
        raise NotImplementedError

    def problema_url(self, url: str) -> str | None:
        """Por qué no se puede pedir esta URL de búsqueda (p. ej. robots.txt del portal), o None si se puede.
        Recibe la plantilla con {zone}/{price_min}/{price_max} sin reemplazar."""
        return None

    def search_urls(self, profile: dict[str, Any]) -> list[tuple[str, str]]:
        """[(etiqueta, url)] de un perfil. Config del portal: `search_urls` (lista explícita) o `zones` +
        `search_url_template`. Cada zona es un string («almagro») o un dict con overrides ({zone, price_min,
        price_max}) para partir una zona grande por precio."""
        cfg = profile["portals"][self.nombre]
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

    # ---- páginas ----
    def parse_page(self, html: str) -> tuple[list[Listing], int | None]:
        """(avisos, total de resultados de la búsqueda o None si la página no lo dice)."""
        raise NotImplementedError

    def es_desafio(self, html: str) -> bool:
        from .common import es_desafio
        return es_desafio(html)

    def miniatura(self, foto: str) -> str:
        """URL de una foto más chica para listados, si la foto es de este portal. Si no, la misma URL."""
        return foto
