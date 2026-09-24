"""Filtros de búsqueda editables desde la web (pantalla Estado): qué busca la ronda y qué se muestra.

Un documento JSON (lo guarda la web) con una o más búsquedas. Cada búsqueda tiene filtros comunes a todos los portales
y, por portal, si está activo, sus zonas, ajustes que pisan algún filtro común y una plantilla de URL opcional:

    {"busquedas": [{"nombre": "caba-apto-credito",
                    "comunes": {"operacion": "compra", "tipo": "departamento", "moneda": "USD",
                                "precio_min": 50000, "precio_max": 125000, "amb_min": 3, "amb_max": None,
                                "dorm_min": 2, "dorm_max": None, "m2_tot_min": 60, "m2_cub_min": None, "apto_credito": True,
                                "excluir": ["pozo", "sin escritura"]},
                    "portales": {"zonaprop": {"activo": True, "zonas": [{"zona": "almagro", "precio_max": 110000}],
                                              "ajustes": {"precio_max": 120000}, "plantilla": None}}}]}

`perfiles()` lo convierte a los perfiles que usan la ronda (inmo.navegador) y matches_profile. `recalcular()` marca
`publicaciones.fuera_filtro` en lo ya guardado: se oculta en la web, pero no se borra (si se aflojan los filtros, vuelve).
"""
from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors.base import Listing
from .connectors.common import matches_profile
from .connectors import PROXIMOS, REGISTRO, etiqueta
from .models import Publicacion



def portales() -> dict[str, str]:
    """{nombre: etiqueta} de los portales que la ronda recorre y de los que se van a sumar (en ese orden)."""
    return {**{n: p.etiqueta for n, p in REGISTRO.items()}, **{n: e for n, e in PROXIMOS.items() if n not in REGISTRO}}


def disponibles() -> set[str]:
    """Portales que la ronda por navegador ya sabe recorrer; los demás se muestran en la web como «etapa 2»."""
    return set(REGISTRO)

NUMERICOS = ("precio_min", "precio_max", "amb_min", "amb_max", "dorm_min", "dorm_max", "m2_tot_min", "m2_tot_max",
             "m2_cub_min", "m2_cub_max")
AJUSTABLES = ("precio_min", "precio_max", "amb_min", "amb_max", "dorm_max", "m2_tot_min", "m2_tot_max")   # los que un portal puede pisar

COMUNES_VACIOS: dict[str, Any] = {"operacion": "compra", "tipo": "departamento", "moneda": "USD",
                                  **{k: None for k in NUMERICOS}, "apto_credito": False, "excluir": []}


def nueva_busqueda(nombre: str = "busqueda") -> dict[str, Any]:
    return {"nombre": nombre, "comunes": copy.deepcopy(COMUNES_VACIOS),
            "portales": {p: {"activo": p in disponibles(), "zonas": [], "ajustes": {}, "plantilla": None} for p in portales()}}


# ---------- importación desde profiles.yaml ----------
def desde_yaml(profiles: list[dict[str, Any]], estricto: bool = True) -> dict[str, Any]:
    """Perfiles del YAML -> documento. La plantilla de Zonaprop se traduce a filtros si es una de las que se generan;
    si no, queda como plantilla avanzada. Con estricto=False no se valida (se importa lo que haya, para corregirlo
    desde la web)."""
    out = []
    for p in profiles:
        b = nueva_busqueda(str(p.get("name") or f"busqueda-{len(out) + 1}"))
        c = b["comunes"]
        c.update(operacion=p.get("operation", "compra"), tipo=p.get("type", "departamento"),
                 moneda=p.get("currency", "USD"), excluir=list(p.get("exclude_keywords") or []),
                 precio_min=p.get("price", {}).get("min"), precio_max=p.get("price", {}).get("max"),
                 amb_min=p.get("rooms", {}).get("min"), amb_max=p.get("rooms", {}).get("max"),
                 dorm_min=p.get("bedrooms", {}).get("min"), dorm_max=p.get("bedrooms", {}).get("max"),
                 m2_tot_min=p.get("total_m2_min"), m2_tot_max=p.get("total_m2_max"),
                 m2_cub_min=p.get("covered_m2_min"), m2_cub_max=p.get("covered_m2_max"))
        for portal, pc in (p.get("portals") or {}).items():
            dest = b["portales"].setdefault(portal, {"activo": True, "zonas": [], "ajustes": {}, "plantilla": None})
            dest["activo"] = True
            dest["zonas"] = [_zona_desde_yaml(z) for z in pc.get("zones") or []]
            if pc.get("search_urls") or pc.get("search_url"):
                dest["urls"] = list(pc.get("search_urls") or [pc["search_url"]])
            tpl = pc.get("search_url_template")
            if tpl and portal in REGISTRO and (f := REGISTRO[portal].leer_plantilla(tpl)):
                c["apto_credito"] = f["apto_credito"]
                c["dorm_min"] = c["dorm_min"] or f["dorm_min"]
                c["amb_min"] = max(x for x in (c["amb_min"], f["amb_min"], 0) if x is not None) or None
                c["amb_max"] = c["amb_max"] or f["amb_max"]
            elif tpl:
                dest["plantilla"] = tpl
        for portal, pc in b["portales"].items():
            if portal not in (p.get("portals") or {}):
                pc["activo"] = False
        out.append(b)
    doc = {"busquedas": out or [nueva_busqueda()]}
    return validar(doc) if estricto else doc


def _zona_desde_yaml(z: Any) -> dict[str, Any]:
    if isinstance(z, dict):
        return {"zona": str(z["zone"]), "precio_min": z.get("price_min"), "precio_max": z.get("price_max")}
    return {"zona": str(z), "precio_min": None, "precio_max": None}


# ---------- zonas como texto (una por línea) ----------
_ZONA_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)(?:\s+(\d*)\s*-\s*(\d*))?$")


def zonas_a_texto(zonas: list[dict[str, Any]]) -> str:
    """[{zona: almagro, precio_max: 110000}] -> 'almagro -110000' (una zona por línea)."""
    lineas = []
    for z in zonas:
        rango = "" if z.get("precio_min") is None and z.get("precio_max") is None else \
            f" {z.get('precio_min') or ''}-{z.get('precio_max') or ''}"
        lineas.append(z["zona"] + rango)
    return "\n".join(lineas)


def zonas_desde_texto(texto: str) -> list[dict[str, Any]]:
    """Una zona por línea, como aparece en la URL del portal («villa-crespo»). Opcional, un rango de precio para partir
    una zona con muchos resultados: «almagro -110000», «almagro 110000-». ValueError con la línea que no se entiende."""
    out = []
    for n, linea in enumerate(texto.splitlines(), 1):
        linea = linea.strip().lower()
        if not linea:
            continue
        m = _ZONA_RE.match(linea)
        if not m:
            raise ValueError(f"Zona de la línea {n} («{linea}»): usá el nombre como en la URL (p. ej. villa-crespo) "
                             "y, si hace falta, un rango de precio: «almagro 50000-110000»")
        out.append({"zona": m.group(1), "precio_min": int(m.group(2)) if m.group(2) else None,
                    "precio_max": int(m.group(3)) if m.group(3) else None})
    return out


# ---------- validación ----------
def validar(doc: dict[str, Any]) -> dict[str, Any]:
    """Normaliza y valida el documento. ValueError con un mensaje para mostrar en la web."""
    busquedas = doc.get("busquedas") or []
    if not busquedas:
        raise ValueError("Tiene que haber al menos una búsqueda")
    nombres = set()
    for b in busquedas:
        b["nombre"] = re.sub(r"[^a-z0-9-]+", "-", str(b.get("nombre") or "").strip().lower()).strip("-")[:40]
        if not b["nombre"]:
            raise ValueError("Cada búsqueda necesita un nombre")
        if b["nombre"] in nombres:
            raise ValueError(f"Hay dos búsquedas con el nombre «{b['nombre']}»")
        nombres.add(b["nombre"])
        c = {**COMUNES_VACIOS, **(b.get("comunes") or {})}
        for k in NUMERICOS:
            c[k] = _numero(c[k], k)
        c["excluir"] = [x.strip() for x in c["excluir"] if str(x).strip()]
        _rangos(c, f"«{b['nombre']}»")
        b["comunes"] = c
        base = nueva_busqueda()["portales"]
        for portal, pc in {**base, **(b.get("portales") or {})}.items():
            pc = {**base.get(portal, {}), **pc}
            pc["ajustes"] = {k: _numero(v, k) for k, v in (pc.get("ajustes") or {}).items() if k in AJUSTABLES and v not in (None, "")}
            pc["plantilla"] = (pc.get("plantilla") or "").strip() or None
            if pc["plantilla"] and "{zone}" not in pc["plantilla"]:
                raise ValueError(f"{etiqueta(portal)}: la plantilla tiene que incluir {{zone}}")
            efectivos = {**c, **pc["ajustes"]}
            _rangos(efectivos, f"«{b['nombre']}» en {etiqueta(portal)}")
            if pc.get("activo") and portal in REGISTRO and not pc.get("urls"):
                if not pc["zonas"]:
                    raise ValueError(f"{etiqueta(portal)} está activo pero no tiene zonas")
                plantilla = pc["plantilla"] or REGISTRO[portal].armar_plantilla(efectivos)   # verificada (ValueError si no)
                if problema := REGISTRO[portal].problema_url(plantilla):                    # p. ej. robots.txt
                    raise ValueError(problema)
                if not pc["plantilla"] and (efectivos["precio_min"] is None or efectivos["precio_max"] is None):
                    raise ValueError(f"{etiqueta(portal)} necesita precio mínimo y máximo (van en la URL)")
            b.setdefault("portales", {})[portal] = pc
    return doc


def _numero(v: Any, campo: str) -> int | None:
    if v in (None, ""):
        return None
    try:
        n = int(float(str(v).replace(".", "").replace(",", "."))) if isinstance(v, str) else int(v)
    except ValueError:
        raise ValueError(f"«{v}» no es un número válido ({campo.replace('_', ' ')})") from None
    if n < 0:
        raise ValueError(f"{campo.replace('_', ' ')} no puede ser negativo")
    return n


def _rangos(c: dict[str, Any], donde: str) -> None:
    for lo, hi, nombre in (("precio_min", "precio_max", "precio"), ("amb_min", "amb_max", "ambientes"),
                           ("dorm_min", "dorm_max", "dormitorios"), ("m2_tot_min", "m2_tot_max", "m² totales"),
                           ("m2_cub_min", "m2_cub_max", "m² cubiertos")):
        if c.get(lo) is not None and c.get(hi) is not None and c[lo] > c[hi]:
            raise ValueError(f"{donde}: el mínimo de {nombre} es mayor que el máximo")


# ---------- documento -> perfiles de la ronda ----------
def perfiles(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Un perfil por búsqueda y portal activo (con los ajustes del portal aplicados), en el formato que usan
    inmo.navegador y matches_profile."""
    out = []
    for b in doc.get("busquedas", []):
        for portal, pc in b["portales"].items():
            if not pc.get("activo") or portal not in REGISTRO:
                continue
            f = {**COMUNES_VACIOS, **b["comunes"], **pc.get("ajustes", {})}   # docs guardados antes de sumar campos
            conf: dict[str, Any] = {}
            if pc.get("urls"):
                conf["search_urls"] = list(pc["urls"])
            else:
                conf["search_url_template"] = pc.get("plantilla") or REGISTRO[portal].armar_plantilla(f)
                conf["zones"] = [{"zone": z["zona"], **({"price_min": z["precio_min"]} if z.get("precio_min") is not None else {}),
                                  **({"price_max": z["precio_max"]} if z.get("precio_max") is not None else {})}
                                 if z.get("precio_min") is not None or z.get("precio_max") is not None else z["zona"]
                                 for z in pc["zonas"]]
            out.append({"name": b["nombre"], "operation": f["operacion"], "type": f["tipo"], "currency": f["moneda"],
                        "price": {"min": f["precio_min"], "max": f["precio_max"]},
                        "rooms": {"min": f["amb_min"], "max": f["amb_max"]}, "bedrooms": {"min": f["dorm_min"], "max": f["dorm_max"]},
                        "total_m2_min": f["m2_tot_min"], "total_m2_max": f["m2_tot_max"],
                        "covered_m2_min": f["m2_cub_min"], "covered_m2_max": f["m2_cub_max"],
                        "exclude_keywords": list(f["excluir"]), "portals": {portal: conf}})
    return out


# ---------- lo ya guardado ----------
_CAMPOS = ("titulo", "descripcion", "precio", "moneda", "ambientes", "dormitorios", "m2_totales", "m2_cubiertos")


def recalcular(s: Session, perfiles_: list[dict[str, Any]]) -> int:
    """Marca fuera_filtro en cada publicación (activa o no) según si cumple algún perfil de su portal. Devuelve cuántas
    publicaciones *activas* quedaron fuera. Un portal sin perfiles (apagado o todavía no disponible) no cambia."""
    por_portal: dict[str, list[dict[str, Any]]] = {}
    for p in perfiles_:
        for portal in p["portals"]:
            por_portal.setdefault(portal, []).append(p)
    fuera = 0
    for pub in s.scalars(select(Publicacion)):
        ps = por_portal.get(pub.portal)
        if ps is None:
            fuera += bool(pub.fuera_filtro and pub.activa)
            continue
        l = Listing(pub.portal, pub.id_externo, pub.url, **{k: getattr(pub, k) for k in _CAMPOS})
        f = not any(matches_profile(l, p) for p in ps)
        if pub.fuera_filtro != f:
            pub.fuera_filtro = f
        fuera += f and pub.activa
    return fuera
