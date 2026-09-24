"""Ronda por navegador: la extensión abre las búsquedas en el navegador del usuario y manda cada página acá; este
módulo decide qué página sigue y guarda los avisos (zonas de los filtros, tope de páginas de robots.txt, filtros del
perfil, bajas sólo tras una ronda completa). Es la única vía de carga: el servidor no pide páginas a los portales.

Una ronda recorre, uno detrás de otro, los portales del registro (connectors.REGISTRO) que tengan búsquedas. Cada
portal lleva su propia cuenta: cooldown tras un bloqueo e intervalo mínimo entre rondas (se evalúan al iniciar: el que
no corresponde se saltea), cadencia (politeness.<portal>), consultas y bajas. Si un portal bloquea a mitad de ronda, se
saltean sus zonas restantes y se sigue con los demás.

Flujo (lo expone la web en /api/navegador/*):
    iniciar()  -> {"ronda": id, "url": primera página, "pausa": s, "portal", "host"}   o {"omitir": motivo}
    pagina()   -> {"siguiente": url, "pausa": s, "portal", "host"}                     o {"fin": True, "mensaje": ...}
    error()    -> igual que pagina(): la zona se da por fallida y se sigue con la próxima
El estado vive en la base (RondaNavegador + Ejecucion), así que el servidor puede reiniciarse entre páginas.
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
from .connectors import PORTAL_HISTORICO, REGISTRO
from .connectors.base import Portal
from .connectors.common import matches_profile
from .models import Consulta, Ejecucion, RondaNavegador

log = logging.getLogger(__name__)
PAUSA_MIN = 30          # la extensión espera con chrome.alarms, que no admite menos de 30 s
ABANDONO = timedelta(minutes=30)   # una ronda sin noticias de la extensión por este tiempo se da por interrumpida
_TIPO = {"nueva": "nuevas", "precio_cambiado": "precio_cambiado", "vista": "vistas"}


def _pol(cfg: dict[str, Any], portal: str) -> dict[str, Any]:
    return cfg.get("politeness", {}).get(portal, {})


def _pausa(cfg: dict[str, Any], portal: str, clave: str) -> int:
    pol = _pol(cfg, portal)
    lo, hi = pol.get(clave) or pol.get("page_delay") or (30, 60)
    return max(PAUSA_MIN, round(random.uniform(lo, hi)))


def _paso(p: Portal, url: str, pausa: int, clave: str = "siguiente") -> dict[str, Any]:
    """Respuesta para la extensión: `portal` y `host` son aditivos (una extensión vieja los ignora)."""
    return {clave: url, "pausa": pausa, "portal": p.nombre, "host": p.host}


def _portal_de(z: dict[str, Any]) -> str:
    return z.get("portal", PORTAL_HISTORICO)    # rondas guardadas antes del multi-portal


def _etiqueta_zona(d: dict[str, Any], z: dict[str, Any]) -> str:
    """«almagro» si la ronda es de un solo portal; «Zonaprop · almagro» si recorre varios."""
    return z["zona"] if len(d["portales"]) == 1 else f"{REGISTRO[_portal_de(z)].etiqueta} · {z['zona']}"


def iniciar(s: Session, cfg: dict[str, Any], usuario: str, now: datetime | None = None,
            forzar: bool = False) -> dict[str, Any]:
    """`forzar` (ronda pedida a mano): ignora el intervalo mínimo entre rondas, pero nunca el cooldown por bloqueo."""
    now = now or datetime.now()
    for e in s.scalars(select(Ejecucion).where(Ejecucion.estado == "corriendo")):
        if s.get(RondaNavegador, e.id) and now - e.actualizado > ABANDONO:   # navegador cerrado a mitad de ronda
            e.estado, e.fin = "interrumpida", now
            e.mensaje = "La extensión dejó de responder (¿se cerró el navegador?). No se dio de baja ningún aviso."
    s.commit()
    if s.scalar(select(Ejecucion).where(Ejecucion.estado == "corriendo").limit(1)):
        return {"omitir": "ya hay una corrida en curso"}

    zonas, omitidos = [], []
    for nombre, p in REGISTRO.items():
        perfiles = [x for x in cfg["profiles"] if nombre in x.get("portals", {})]
        if not perfiles:
            continue
        pol = _pol(cfg, nombre)
        until = repo.blocked_until(s, nombre, pol.get("cooldown_hours_on_block", 24))
        if until and now < until:
            omitidos.append(f"{p.etiqueta}: cooldown por bloqueo hasta {until:%d/%m %H:%M}")
            continue
        last = repo.last_query(s, nombre)
        gap = timedelta(hours=pol.get("min_hours_between_runs", 20))
        if last and now - last.fecha < gap and not forzar:
            omitidos.append(f"{p.etiqueta}: última consulta {last.fecha:%d/%m %H:%M} (mín. {gap} entre rondas)")
            continue
        zonas += [{"portal": nombre, "perfil": x["name"], "zona": z, "url": u} for x in perfiles for z, u in p.search_urls(x)]
    if not zonas:
        return {"omitir": "; ".join(omitidos) if omitidos else "ningún perfil tiene un portal disponible"}

    portales = list(dict.fromkeys(z["portal"] for z in zonas))
    d = {"zonas": zonas, "idx": 0, "pagina": 1, "paginas": None, "zona_vistos": [], "errores": [], "omitidos": omitidos,
         "portales": {n: {"completa": True, "truncada": False, "bloqueada": False,
                          "vistos": {x: [] for x in dict.fromkeys(z["perfil"] for z in zonas if z["portal"] == n)},
                          "stats": {x: {"nuevas": 0, "precio_cambiado": 0, "vistas": 0}
                                    for x in dict.fromkeys(z["perfil"] for z in zonas if z["portal"] == n)}}
                      for n in portales}}
    e = Ejecucion(portal=",".join(portales)[:32], zonas=[], usuario=f"{usuario} (navegador)", inicio=now,
                  actualizado=now, estado="corriendo", zonas_total=len(zonas), zona_idx=1,
                  zona_actual=_etiqueta_zona(d, zonas[0])[:80])
    s.add(e)
    s.flush()
    s.add(RondaNavegador(id=e.id, datos=d))
    s.commit()
    log.info("ronda por navegador %d: %d búsqueda(s) en %s%s", e.id, len(zonas), ", ".join(portales),
             f" (omitidos: {'; '.join(omitidos)})" if omitidos else "")
    return {"ronda": e.id, **_paso(REGISTRO[zonas[0]["portal"]], zonas[0]["url"], 0, "url")}


def _cargar(s: Session, ronda: int) -> tuple[Ejecucion | None, RondaNavegador | None]:
    return s.get(Ejecucion, ronda), s.get(RondaNavegador, ronda)


def _normalizar(datos: dict) -> dict:
    """Copia de trabajo del estado. Las rondas guardadas antes del multi-portal (todo suelto, sólo Zonaprop) se
    pasan al formato por portal."""
    d = copy.deepcopy(datos)
    if "portales" not in d:
        d["portales"] = {PORTAL_HISTORICO: {"completa": d.pop("completa", True), "truncada": d.pop("truncada", False),
                                      "bloqueada": False, "vistos": d.pop("vistos", {}), "stats": d.pop("stats", {})}}
        d.setdefault("omitidos", [])
    return d


def _fin(e: Ejecucion | None) -> dict[str, Any]:
    return {"fin": True, "estado": e.estado if e else "desconocida", "mensaje": (e.mensaje if e else None) or ""}


def pagina(s: Session, cfg: dict[str, Any], ronda: int, url: str, html: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    e, r = _cargar(s, ronda)
    if not e or not r or e.estado != "corriendo":
        return _fin(e)                                   # cancelada, interrumpida o ya terminada
    d = _normalizar(r.datos)
    z = d["zonas"][d["idx"]]
    nombre = _portal_de(z)
    p, est = REGISTRO[nombre], d["portales"][nombre]
    esperada = p.page_url(z["url"], d["pagina"])
    if url != esperada:                                  # desfasada (p. ej. reintento): pedir la correcta
        return _paso(p, esperada, PAUSA_MIN)
    if p.es_desafio(html):
        est.update(completa=False, bloqueada=True)
        d["errores"].append(f"[{_etiqueta_zona(d, z)}] desafío anti-bot en {url}")
        return _siguiente_zona(s, cfg, e, r, d, now)     # se saltean las zonas que le queden a este portal

    listings, total = p.parse_page(html)
    if d["pagina"] == 1:
        if total is None and not listings:
            return _fallar_zona(s, cfg, e, r, d, now, "la página no tiene avisos reconocibles (¿cambió el sitio?)")
        tope = min(_pol(cfg, nombre).get("max_pages", p.max_pages), p.max_pages)
        d["paginas"] = min(math.ceil(total / p.page_size), tope) if total is not None else tope
        if total is not None and total > d["paginas"] * p.page_size:
            est["truncada"] = True
            d["errores"].append(f"[{_etiqueta_zona(d, z)}] {total} resultados pero solo se pueden leer "
                                f"{d['paginas'] * p.page_size} (robots.txt/max_pages): dividir esta zona en búsquedas más chicas")

    zona_vistos = set(d["zona_vistos"])
    nuevos = [l for l in listings if l.id_externo not in zona_vistos]
    perfil = next(x for x in cfg["profiles"] if x["name"] == z["perfil"] and nombre in x.get("portals", {}))
    todos = {i for ids in est["vistos"].values() for i in ids}        # por portal: los ids de cada sitio son propios
    for l in nuevos:
        zona_vistos.add(l.id_externo)
        if l.id_externo not in todos and matches_profile(l, perfil):
            todos.add(l.id_externo)
            est["vistos"][z["perfil"]].append(l.id_externo)
            _, tipo = repo.upsert(s, l, now)
            est["stats"][z["perfil"]][_TIPO[tipo]] += 1
    d["zona_vistos"] = sorted(zona_vistos)
    e.pagina, e.paginas, e.actualizado = d["pagina"], d["paginas"] or 0, now
    e.resultados = sum(len(ids) for x in d["portales"].values() for ids in x["vistos"].values())

    if nuevos and d["pagina"] < (d["paginas"] or 1):
        d["pagina"] += 1
        return _guardar(s, r, d, _paso(p, p.page_url(z["url"], d["pagina"]), _pausa(cfg, nombre, "page_delay")))
    return _siguiente_zona(s, cfg, e, r, d, now)


def error(s: Session, cfg: dict[str, Any], ronda: int, url: str, motivo: str, now: datetime | None = None) -> dict[str, Any]:
    """La extensión no pudo cargar la página (tiempo de espera, error de red): se da la zona por fallida."""
    now = now or datetime.now()
    e, r = _cargar(s, ronda)
    if not e or not r or e.estado != "corriendo":
        return _fin(e)
    return _fallar_zona(s, cfg, e, r, _normalizar(r.datos), now, f"{motivo[:200]} ({url})")


def cancelar(s: Session, ronda: int, now: datetime | None = None) -> dict[str, Any]:
    """Cancelada desde la extensión: lo recibido queda guardado, pero no se registra consulta ni se dan bajas."""
    e = s.get(Ejecucion, ronda)
    if e and e.estado == "corriendo" and s.get(RondaNavegador, ronda):
        e.estado, e.fin = "cancelada", now or datetime.now()
        e.mensaje = "Cancelada desde la extensión: se guardaron las páginas ya recibidas; no se dio de baja ningún aviso."
        s.commit()
    return _fin(e)


def _fallar_zona(s, cfg, e, r, d, now, motivo: str) -> dict[str, Any]:
    z = d["zonas"][d["idx"]]
    d["portales"][_portal_de(z)]["completa"] = False
    d["errores"].append(f"[{_etiqueta_zona(d, z)}] {motivo}")
    return _siguiente_zona(s, cfg, e, r, d, now)


def _siguiente_zona(s, cfg, e, r, d, now) -> dict[str, Any]:
    d.update(idx=d["idx"] + 1, pagina=1, paginas=None, zona_vistos=[])
    while d["idx"] < len(d["zonas"]) and d["portales"][_portal_de(d["zonas"][d["idx"]])]["bloqueada"]:
        d["idx"] += 1                                    # zonas de un portal que bloqueó: no se insiste
    if d["idx"] >= len(d["zonas"]):
        return _terminar(s, cfg, e, r, d, now)
    z = d["zonas"][d["idx"]]
    nombre = _portal_de(z)
    e.zona_idx, e.zona_actual, e.pagina, e.paginas, e.actualizado = d["idx"] + 1, _etiqueta_zona(d, z)[:80], 0, 0, now
    return _guardar(s, r, d, _paso(REGISTRO[nombre], z["url"], _pausa(cfg, nombre, "zone_delay")))


def _guardar(s: Session, r: RondaNavegador, d: dict, respuesta: dict) -> dict:
    r.datos = d
    flag_modified(r, "datos")                            # SQLAlchemy no detecta cambios dentro del JSON
    s.commit()
    return respuesta


def _terminar(s, cfg, e, r, d, now) -> dict[str, Any]:
    errores = "\n".join(d["errores"]) or None
    lineas_portal, bajas_total = [], 0
    for nombre, est in d["portales"].items():
        completa = est["completa"] and not est["bloqueada"]
        for perfil, ids in est["vistos"].items():
            s.add(Consulta(perfil=perfil, portal=nombre, fecha=now, cantidad_resultados=len(ids),
                           completa=completa, bloqueada=est["bloqueada"], errores=errores))
        bajas = 0
        if completa and not est["truncada"]:            # truncada => "no visto" no implica baja
            bajas = repo.mark_missing(s, nombre, {i for ids in est["vistos"].values() for i in ids}, INACTIVE_AFTER)
        bajas_total += bajas
        st = {k: sum(x[k] for x in est["stats"].values()) for k in ("nuevas", "precio_cambiado")}
        n = sum(len(ids) for ids in est["vistos"].values())
        estado = "bloqueado" if est["bloqueada"] else ("completo" if completa else "parcial")
        lineas_portal.append(f"{REGISTRO[nombre].etiqueta}: {n} avisos, {st['nuevas']} nuevos, "
                             f"{st['precio_cambiado']} con cambio de precio, {bajas} dados de baja ({estado})")
    try:
        tags.aplicar(s, cfg.get("auto_tags"))
    except ValueError as ex:
        log.error("etiquetas automáticas: %s", ex)
    todos = d["portales"].values()
    st = {k: sum(x[k] for est in todos for x in est["stats"].values()) for k in ("nuevas", "precio_cambiado")}
    total = sum(len(ids) for est in todos for ids in est["vistos"].values())
    lineas = [f"{total} avisos: {st['nuevas']} nuevos, {st['precio_cambiado']} con cambio de precio, "
              f"{bajas_total} dados de baja"]
    if len(d["portales"]) > 1:
        lineas += lineas_portal
    lineas += d["errores"] + [f"Omitido — {o}" for o in d.get("omitidos", [])]
    bloqueados = [est["bloqueada"] for est in todos]
    e.estado = ("bloqueada" if all(bloqueados) else "parcial") if any(bloqueados) else \
        ("ok" if all(est["completa"] for est in todos) else "parcial")
    e.fin = e.actualizado = now
    e.mensaje = "\n".join(lineas)
    r.datos = d
    flag_modified(r, "datos")
    s.commit()
    log.info("ronda por navegador %d: %s", e.id, e.mensaje.replace("\n", " | "))
    return _fin(e)
