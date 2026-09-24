"""Ronda por navegador: la extensión abre las búsquedas de Zonaprop en el navegador del usuario y manda cada página
acá; este módulo decide qué página sigue y guarda los avisos (zonas del YAML, tope de páginas de robots.txt,
filtros del perfil, bajas sólo tras una ronda completa). Es la única vía de carga: el servidor no pide páginas a los
portales.

Flujo (lo expone la web en /api/navegador/*):
    iniciar()  -> {"ronda": id, "url": primera página, "pausa": s}   o {"omitir": motivo}
    pagina()   -> {"siguiente": url, "pausa": s}                     o {"fin": True, "mensaje": ...}
    error()    -> igual que pagina(): la zona se da por fallida y se sigue con la próxima
El estado vive en la base (RondaNavegador + Ejecucion), así que el servidor puede reiniciarse entre páginas
sólo a costa de cortar la ronda en curso (queda «interrumpida»).
"""
from __future__ import annotations

import copy
import logging
import math
import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from . import repo, tags
from .config import INACTIVE_AFTER
from .connectors.common import es_desafio, matches_profile
from .connectors.zonaprop import PAGE_SIZE, ROBOTS_MAX_PAGES, page_url, parse_listing_page, search_urls
from .models import Consulta, Ejecucion, RondaNavegador

log = logging.getLogger(__name__)
PORTAL = "zonaprop"
PAUSA_MIN = 30          # la extensión espera con chrome.alarms, que no admite menos de 30 s
ABANDONO = timedelta(minutes=30)   # una ronda sin noticias de la extensión por este tiempo se da por interrumpida


def _pol(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("politeness", {}).get(PORTAL, {})


def _pausa(cfg: dict[str, Any], clave: str) -> int:
    pol = _pol(cfg)
    lo, hi = pol.get(clave) or pol.get("page_delay") or (30, 60)
    return max(PAUSA_MIN, round(random.uniform(lo, hi)))


def iniciar(s: Session, cfg: dict[str, Any], usuario: str, now: datetime | None = None,
            forzar: bool = False) -> dict[str, Any]:
    """`forzar` (ronda pedida a mano): ignora el intervalo mínimo entre rondas, pero nunca el cooldown por bloqueo."""
    now = now or datetime.now()
    pol = _pol(cfg)
    until = repo.blocked_until(s, PORTAL, pol.get("cooldown_hours_on_block", 24))
    if until and now < until:
        return {"omitir": f"cooldown por bloqueo hasta {until:%d/%m %H:%M}"}
    last = repo.last_query(s, PORTAL)
    gap = timedelta(hours=pol.get("min_hours_between_runs", 20))
    if last and now - last.fecha < gap and not forzar:
        return {"omitir": f"última consulta {last.fecha:%d/%m %H:%M} (mín. {gap} entre rondas)"}
    for e in s.scalars(select(Ejecucion).where(Ejecucion.estado == "corriendo")):
        if s.get(RondaNavegador, e.id) and now - e.actualizado > ABANDONO:   # navegador cerrado a mitad de ronda
            e.estado, e.fin = "interrumpida", now
            e.mensaje = "La extensión dejó de responder (¿se cerró el navegador?). No se dio de baja ningún aviso."
    s.commit()
    if s.scalar(select(Ejecucion).where(Ejecucion.estado == "corriendo").limit(1)):
        return {"omitir": "ya hay una corrida en curso"}
    zonas = [{"perfil": p["name"], "zona": z, "url": u}
             for p in cfg["profiles"] if PORTAL in p.get("portals", {}) for z, u in search_urls(p)]
    if not zonas:
        return {"omitir": f"ningún perfil tiene el portal {PORTAL}"}
    e = Ejecucion(portal=PORTAL, zonas=[], usuario=f"{usuario} (navegador)", inicio=now, actualizado=now,
                  estado="corriendo", zonas_total=len(zonas), zona_idx=1, zona_actual=zonas[0]["zona"])
    s.add(e)
    s.flush()
    perfiles = sorted({z["perfil"] for z in zonas})
    s.add(RondaNavegador(id=e.id, datos={
        "zonas": zonas, "idx": 0, "pagina": 1, "paginas": None, "zona_vistos": [],
        "vistos": {p: [] for p in perfiles}, "stats": {p: {"nuevas": 0, "precio_cambiado": 0, "vistas": 0} for p in perfiles},
        "completa": True, "truncada": False, "errores": [],
    }))
    s.commit()
    log.info("ronda por navegador %d: %d búsqueda(s)", e.id, len(zonas))
    return {"ronda": e.id, "url": zonas[0]["url"], "pausa": 0}


def _cargar(s: Session, ronda: int) -> tuple[Ejecucion | None, RondaNavegador | None]:
    return s.get(Ejecucion, ronda), s.get(RondaNavegador, ronda)


def _fin(e: Ejecucion | None) -> dict[str, Any]:
    return {"fin": True, "estado": e.estado if e else "desconocida", "mensaje": (e.mensaje if e else None) or ""}


def pagina(s: Session, cfg: dict[str, Any], ronda: int, url: str, html: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    e, r = _cargar(s, ronda)
    if not e or not r or e.estado != "corriendo":
        return _fin(e)                                   # cancelada, interrumpida o ya terminada
    d = copy.deepcopy(r.datos)
    z = d["zonas"][d["idx"]]
    esperada = page_url(z["url"], d["pagina"])
    if url != esperada:                                  # desfasada (p. ej. reintento): pedir la correcta
        return {"siguiente": esperada, "pausa": PAUSA_MIN}
    if es_desafio(html):
        d["completa"] = False
        d["errores"].append(f"[{z['zona']}] desafío anti-bot en {url}")
        return _terminar(s, cfg, e, r, d, now, bloqueada=True)

    listings, total = parse_listing_page(html)
    if d["pagina"] == 1:
        if total is None and not listings:
            return _fallar_zona(s, cfg, e, r, d, now, "la página no tiene avisos reconocibles (¿cambió el sitio?)")
        tope = min(_pol(cfg).get("max_pages", ROBOTS_MAX_PAGES), ROBOTS_MAX_PAGES)
        d["paginas"] = min(math.ceil(total / PAGE_SIZE), tope) if total is not None else tope
        if total is not None and total > d["paginas"] * PAGE_SIZE:
            d["truncada"] = True
            d["errores"].append(f"[{z['zona']}] {total} resultados pero solo se pueden leer {d['paginas'] * PAGE_SIZE} "
                                "(robots.txt/max_pages): dividir esta zona en búsquedas más chicas")

    zona_vistos = set(d["zona_vistos"])
    nuevos = [l for l in listings if l.id_externo not in zona_vistos]
    perfil = next(p for p in cfg["profiles"] if p["name"] == z["perfil"])
    todos = {i for ids in d["vistos"].values() for i in ids}
    for l in nuevos:
        zona_vistos.add(l.id_externo)
        if l.id_externo not in todos and matches_profile(l, perfil):
            todos.add(l.id_externo)
            d["vistos"][z["perfil"]].append(l.id_externo)
            _, tipo = repo.upsert(s, l, now)
            d["stats"][z["perfil"]][{"nueva": "nuevas", "precio_cambiado": "precio_cambiado", "vista": "vistas"}[tipo]] += 1
    d["zona_vistos"] = sorted(zona_vistos)
    e.pagina, e.paginas, e.resultados, e.actualizado = d["pagina"], d["paginas"] or 0, len(todos), now

    if nuevos and d["pagina"] < (d["paginas"] or 1):
        d["pagina"] += 1
        return _guardar(s, r, d, {"siguiente": page_url(z["url"], d["pagina"]), "pausa": _pausa(cfg, "page_delay")})
    return _siguiente_zona(s, cfg, e, r, d, now)


def error(s: Session, cfg: dict[str, Any], ronda: int, url: str, motivo: str, now: datetime | None = None) -> dict[str, Any]:
    """La extensión no pudo cargar la página (tiempo de espera, error de red): se da la zona por fallida."""
    now = now or datetime.now()
    e, r = _cargar(s, ronda)
    if not e or not r or e.estado != "corriendo":
        return _fin(e)
    return _fallar_zona(s, cfg, e, r, copy.deepcopy(r.datos), now, f"{motivo[:200]} ({url})")


def cancelar(s: Session, ronda: int, now: datetime | None = None) -> dict[str, Any]:
    """Cancelada desde la extensión: lo recibido queda guardado, pero no se registra consulta ni se dan bajas."""
    e = s.get(Ejecucion, ronda)
    if e and e.estado == "corriendo" and s.get(RondaNavegador, ronda):
        e.estado, e.fin = "cancelada", now or datetime.now()
        e.mensaje = "Cancelada desde la extensión: se guardaron las páginas ya recibidas; no se dio de baja ningún aviso."
        s.commit()
    return _fin(e)


def _fallar_zona(s, cfg, e, r, d, now, motivo: str) -> dict[str, Any]:
    d["completa"] = False
    d["errores"].append(f"[{d['zonas'][d['idx']]['zona']}] {motivo}")
    return _siguiente_zona(s, cfg, e, r, d, now)


def _siguiente_zona(s, cfg, e, r, d, now) -> dict[str, Any]:
    d.update(idx=d["idx"] + 1, pagina=1, paginas=None, zona_vistos=[])
    if d["idx"] >= len(d["zonas"]):
        return _terminar(s, cfg, e, r, d, now)
    z = d["zonas"][d["idx"]]
    e.zona_idx, e.zona_actual, e.pagina, e.paginas, e.actualizado = d["idx"] + 1, z["zona"], 0, 0, now
    return _guardar(s, r, d, {"siguiente": z["url"], "pausa": _pausa(cfg, "zone_delay")})


def _guardar(s: Session, r: RondaNavegador, d: dict, respuesta: dict) -> dict:
    r.datos = d
    flag_modified(r, "datos")                            # SQLAlchemy no detecta cambios dentro del JSON
    s.commit()
    return respuesta


def _terminar(s, cfg, e, r, d, now, bloqueada: bool = False) -> dict[str, Any]:
    completa = d["completa"] and not bloqueada
    errores = "\n".join(d["errores"]) or None
    for perfil, ids in d["vistos"].items():
        s.add(Consulta(perfil=perfil, portal=PORTAL, fecha=now, cantidad_resultados=len(ids),
                       completa=completa, bloqueada=bloqueada, errores=errores))
    desactivadas = 0
    if completa and not d["truncada"]:                  # truncada => "no visto" no implica baja
        desactivadas = repo.mark_missing(s, PORTAL, {i for ids in d["vistos"].values() for i in ids}, INACTIVE_AFTER)
    try:
        tags.aplicar(s, cfg.get("auto_tags"))
    except ValueError as ex:
        log.error("etiquetas automáticas: %s", ex)
    st = {k: sum(x[k] for x in d["stats"].values()) for k in ("nuevas", "precio_cambiado")}
    total = sum(len(ids) for ids in d["vistos"].values())
    lineas = [f"{total} avisos: {st['nuevas']} nuevos, {st['precio_cambiado']} con cambio de precio, "
              f"{desactivadas} dados de baja"] + d["errores"]
    e.estado = "bloqueada" if bloqueada else ("ok" if completa else "parcial")
    e.fin = e.actualizado = now
    e.mensaje = "\n".join(lineas)
    r.datos = d
    flag_modified(r, "datos")
    s.commit()
    log.info("ronda por navegador %d: %s", e.id, e.mensaje.replace("\n", " | "))
    return _fin(e)
