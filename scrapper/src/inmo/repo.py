"""Persistencia: upsert de publicaciones, historial de precios, inactivación y vínculo entre portales."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors.base import Listing
from .models import Categorizacion, Consulta, HistorialPrecio, Inmobiliaria, Publicacion

_FIELDS = ("url", "titulo", "direccion", "barrio", "precio", "moneda", "expensas", "moneda_expensas",
           "ambientes", "dormitorios", "banos", "cocheras", "m2_cubiertos", "m2_totales", "descripcion", "fotos",
           "lat", "lng")


def _inmobiliaria(s: Session, l: Listing, now: datetime) -> Inmobiliaria | None:
    if not l.inmobiliaria_id_externo:
        return None
    e = s.scalar(select(Inmobiliaria).where(Inmobiliaria.portal == l.portal,
                                            Inmobiliaria.id_externo == l.inmobiliaria_id_externo))
    if e is None:
        e = Inmobiliaria(portal=l.portal, id_externo=l.inmobiliaria_id_externo, nombre=l.inmobiliaria_nombre,
                         logo_url=l.inmobiliaria_logo, fecha_primera_vista=now)
        s.add(e)
        s.flush()
    elif l.inmobiliaria_logo and e.logo_url != l.inmobiliaria_logo:
        e.logo_url = l.inmobiliaria_logo
    return e


def upsert(s: Session, l: Listing, now: datetime) -> tuple[Publicacion, str]:
    """Devuelve (publicación, 'nueva' | 'precio_cambiado' | 'vista')."""
    p = s.scalar(select(Publicacion).where(Publicacion.portal == l.portal, Publicacion.id_externo == l.id_externo))
    data = asdict(l)
    emp = _inmobiliaria(s, l, now)
    if p is None:
        p = Publicacion(portal=l.portal, id_externo=l.id_externo, fecha_primera_vista=now,
                        fecha_ultima_vista=now, activa=True, consultas_sin_ver=0,
                        corredor=l.corredor, inmobiliaria_id=emp.id if emp else None,
                        **{k: data[k] for k in _FIELDS})
        p.categorizacion = Categorizacion(estado="nuevo", etiquetas=[], etiquetas_auto=[], fecha_modificacion=now)
        if l.precio is not None and l.moneda:
            p.historial.append(HistorialPrecio(precio=l.precio, moneda=l.moneda, fecha=now))
        s.add(p)
        s.flush()
        link_duplicates(s, p)
        return p, "nueva"

    changed = l.precio is not None and l.moneda and (p.precio != l.precio or p.moneda != l.moneda)
    prev_price, prev_cur = p.precio, p.moneda
    for k in _FIELDS:
        v = data[k]
        # No pisar con None un dato que antes teníamos (p. ej. m² cubiertos obtenidos de otra fuente).
        if v is not None and v != []:
            setattr(p, k, v)
    if emp:
        p.inmobiliaria_id = emp.id
    if l.corredor:
        p.corredor = l.corredor
    p.fecha_ultima_vista, p.activa, p.consultas_sin_ver = now, True, 0
    if changed:
        p.historial.append(HistorialPrecio(precio=l.precio, moneda=l.moneda, fecha=now,
                                           variacion_pct=_variation(prev_price, prev_cur, l.precio, l.moneda)))
    return p, "precio_cambiado" if changed else "vista"


def _variation(old: float | None, old_cur: str | None, new: float, new_cur: str) -> float | None:
    """% vs. precio anterior: negativo = baja, positivo = suba. None si no hay anterior o cambió la moneda."""
    if not old or old_cur != new_cur:
        return None
    return round((new - old) / old * 100, 2)


def mark_missing(s: Session, portal: str, seen_ids: set[str], inactive_after: int) -> int:
    """Tras una consulta COMPLETA: suma 1 a los no vistos y desactiva los que llegan a N. Devuelve # desactivados."""
    pubs = s.scalars(select(Publicacion).where(Publicacion.portal == portal, Publicacion.activa.is_(True))).all()
    n = 0
    for p in pubs:
        if p.id_externo in seen_ids:
            continue
        p.consultas_sin_ver += 1
        if p.consultas_sin_ver >= inactive_after:
            p.activa = False
            n += 1
    return n


def _norm(t: str | None) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\bal\b", " ", t)).split())


def _close(a: float | None, b: float | None, tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol * max(a, b)


def _same_barrio(a: str | None, b: str | None) -> bool:
    """'Almagro Norte' ≈ 'Almagro' ≈ 'almagro': los portales nombran distinto los barrios/sub-barrios."""
    na, nb = _norm(a), _norm(b)
    return bool(na and nb) and (na == nb or na.startswith(nb + " ") or nb.startswith(na + " "))


def link_duplicates(s: Session, p: Publicacion) -> None:
    """Misma dirección normalizada + m² (±3%) + precio (±5%, misma moneda) en otro portal => mismo grupo."""
    addr = _norm(p.direccion)
    if not addr or p.precio is None:
        return
    for o in s.scalars(select(Publicacion).where(
            Publicacion.portal != p.portal, Publicacion.moneda == p.moneda,
            Publicacion.precio.between(p.precio * 0.95, p.precio * 1.05))):
        if (_same_barrio(o.barrio, p.barrio) and _norm(o.direccion) == addr and _close(o.precio, p.precio, 0.05)
                and _close(o.m2_totales or o.m2_cubiertos, p.m2_totales or p.m2_cubiertos, 0.03)):
            p.grupo_id = o.grupo_id or o.id
            o.grupo_id = p.grupo_id
            return


def last_query(s: Session, portal: str) -> Consulta | None:
    return s.scalar(select(Consulta).where(Consulta.portal == portal).order_by(Consulta.fecha.desc()).limit(1))


def blocked_until(s: Session, portal: str, cooldown_hours: float) -> datetime | None:
    q = s.scalar(select(Consulta).where(Consulta.portal == portal, Consulta.bloqueada.is_(True))
                 .order_by(Consulta.fecha.desc()).limit(1))
    return q.fecha + timedelta(hours=cooldown_hours) if q else None
