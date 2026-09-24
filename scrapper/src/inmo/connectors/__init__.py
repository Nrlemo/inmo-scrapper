"""Registro de portales. Para sumar uno: connectors/<portal>.py con una subclase de Portal (base.py), agregarla a
REGISTRO y sacarlo de PROXIMOS. La ronda por navegador, los filtros y la web sólo usan este registro."""
from .base import Listing, Portal
from .zonaprop import PORTAL as _zonaprop

# Portales que la ronda ya sabe recorrer, en el orden en que los recorre
REGISTRO: dict[str, Portal] = {p.nombre: p for p in (_zonaprop,)}
# Datos anteriores al multi-portal (rondas guardadas sin portal por zona) son de este portal: era el único
PORTAL_HISTORICO = _zonaprop.nombre
# Los que se van a sumar (se muestran en Estado → Búsqueda como «etapa 2»)
PROXIMOS = {"argenprop": "Argenprop", "mercadolibre": "MercadoLibre"}


def portal(nombre: str) -> Portal:
    return REGISTRO[nombre]


def etiqueta(nombre: str) -> str:
    return REGISTRO[nombre].etiqueta if nombre in REGISTRO else PROXIMOS.get(nombre, nombre)


def miniatura(foto: str | None) -> str:
    """Foto más chica para listados, según el portal de donde viene (cada uno reconoce su CDN)."""
    foto = foto or ""
    for p in REGISTRO.values():
        chica = p.miniatura(foto)
        if chica != foto:
            return chica
    return foto


__all__ = ["Listing", "Portal", "REGISTRO", "PROXIMOS", "PORTAL_HISTORICO", "portal", "etiqueta", "miniatura"]
