"""Interfaz común de conectores. Un módulo por portal; se registran en connectors/__init__.py."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


from ..errors import BlockedError  # noqa: F401


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


@dataclass
class SearchResult:
    listings: list[Listing] = field(default_factory=list)
    completa: bool = True          # False si se cortó por error/bloqueo
    errores: list[str] = field(default_factory=list)
    bloqueada: bool = False
    truncada: bool = False         # True si algún resultado quedó fuera por el tope de páginas


class Connector(ABC):
    portal: str
    progress = None  # callable(**campos) opcional; lo asigna el runner para informar avance

    def _notify(self, **kw) -> None:
        if self.progress:
            self.progress(**kw)

    @abstractmethod
    def search(self, profile: dict[str, Any]) -> SearchResult:
        """Ejecuta la búsqueda del perfil. No debe lanzar: devuelve errores en SearchResult."""
