"""Aviso normalizado que devuelven los parsers de cada portal (un módulo por portal en connectors/)."""
from __future__ import annotations

from dataclasses import dataclass, field


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
