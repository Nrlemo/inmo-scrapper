"""Filtros de búsqueda guardados en la base (pantalla Estado): los lee la ronda por navegador y deciden qué se muestra.

profiles.yaml sigue aportando la cortesía por portal (`politeness`) y las etiquetas automáticas (`auto_tags`); sus
`profiles` se importan una sola vez, la primera vez que se piden los filtros.
"""
import copy
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from inmo import filtros
from inmo.config import load_config

from . import config
from .models_web import ConfigBusqueda

log = logging.getLogger("inmo.busqueda")


def _yaml() -> dict:
    try:
        return load_config(config.CONFIG_PATH)
    except (OSError, ValueError, KeyError):      # sin profiles.yaml o mal formado
        return {"profiles": [], "politeness": {}}


def obtener(s: Session) -> dict:
    """Documento de filtros (ver inmo.filtros). Si todavía no hay, lo importa de profiles.yaml y lo guarda."""
    fila = s.get(ConfigBusqueda, 1)
    if fila is None:
        perfiles = _yaml().get("profiles") or []
        try:
            doc = filtros.desde_yaml(perfiles)
        except ValueError as e:                  # se importa igual: se corrige desde Estado
            log.warning("filtros importados de %s con errores (corregirlos en Estado): %s", config.CONFIG_PATH, e)
            doc = filtros.desde_yaml(perfiles, estricto=False)
        fila = ConfigBusqueda(id=1, datos=doc, modificado_por="importado de profiles.yaml", fecha=datetime.now())
        s.add(fila)
        s.commit()
        log.info("filtros de búsqueda importados de %s", config.CONFIG_PATH)
    return copy.deepcopy(fila.datos)


def info(s: Session) -> ConfigBusqueda | None:
    return s.get(ConfigBusqueda, 1)


def guardar(s: Session, doc: dict, usuario: str) -> int:
    """Valida, guarda y recalcula qué avisos quedan fuera de filtros. Devuelve cuántos. ValueError si no valida."""
    doc = filtros.validar(copy.deepcopy(doc))
    fila = s.get(ConfigBusqueda, 1) or ConfigBusqueda(id=1)
    fila.datos, fila.modificado_por, fila.fecha = doc, usuario, datetime.now()
    s.add(fila)
    fuera = filtros.recalcular(s, filtros.perfiles(doc))
    s.commit()
    log.info("filtros de búsqueda guardados por %s: %d avisos fuera de filtros", usuario, fuera)
    return fuera


def config_efectiva(s: Session) -> dict:
    """La configuración que usa la ronda: profiles.yaml con los perfiles reemplazados por los filtros de la base."""
    cfg = _yaml()
    cfg["profiles"] = filtros.perfiles(obtener(s))
    return cfg
