from .base import Listing

# Un módulo por portal con sus parsers (URLs de búsqueda, paginación, parseo de la página). La ronda por navegador
# (inmo.navegador) los usa para procesar las páginas que manda la extensión.
__all__ = ["Listing"]
