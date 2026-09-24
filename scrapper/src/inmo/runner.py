"""Orquesta una consulta: chequea cooldown/intervalo, ejecuta el conector, persiste y registra la consulta."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .config import INACTIVE_AFTER
from .connectors import REGISTRY
from .models import Consulta
from . import repo, tags

log = logging.getLogger(__name__)


def run(engine, cfg: dict[str, Any], portal: str, profile_name: str | None = None,
        force: bool = False, zones: list[str] | None = None, now: datetime | None = None, connector=None,
        tracker=None, skip_gap: bool = False) -> dict[str, Any]:
    now = now or datetime.now()
    pol = cfg["politeness"].get(portal, {})
    profiles = [p for p in cfg["profiles"] if portal in p.get("portals", {})
                and (profile_name is None or p["name"] == profile_name)]
    if not profiles:
        raise SystemExit(f"Ningún perfil con portal '{portal}'" + (f" y nombre '{profile_name}'" if profile_name else ""))
    summary: dict[str, Any] = {}
    Session_ = sessionmaker(engine)
    for profile in profiles:
        with Session_() as s:
            if not force:
                until = repo.blocked_until(s, portal, pol.get("cooldown_hours_on_block", 24))
                if until and now < until:
                    summary[profile["name"]] = f"omitido: cooldown por bloqueo hasta {until:%Y-%m-%d %H:%M}"
                    continue
                last = None if skip_gap else repo.last_query(s, portal)  # skip_gap: ignora sólo el intervalo mínimo
                gap = timedelta(hours=pol.get("min_hours_between_runs", 20))
                if last and now - last.fecha < gap:
                    summary[profile["name"]] = f"omitido: última consulta {last.fecha:%Y-%m-%d %H:%M} (mín. {gap} entre corridas)"
                    continue
            if zones:  # corrida parcial: solo las zonas pedidas
                zp = dict(profile["portals"][portal])
                zp["zones"] = [z for z in zp.get("zones", []) if (z["zone"] if isinstance(z, dict) else z) in zones]
                if not zp["zones"]:
                    raise SystemExit(f"Ninguna zona coincide con {zones}")
                profile = {**profile, "portals": {**profile["portals"], portal: zp}}
            conn = connector or REGISTRY[portal](pol)
            if tracker:
                conn.progress = tracker.update
            res = conn.search(profile)
            stats = {"nuevas": 0, "precio_cambiado": 0, "vistas": 0, "desactivadas": 0}
            for l in res.listings:
                _, kind = repo.upsert(s, l, now)
                stats[{"nueva": "nuevas", "precio_cambiado": "precio_cambiado", "vista": "vistas"}[kind]] += 1
            if res.completa and not res.truncada and not zones:  # parcial/truncada: con resultados truncados, "no visto" no implica "dado de baja"
                stats["desactivadas"] = repo.mark_missing(s, portal, {l.id_externo for l in res.listings}, INACTIVE_AFTER)
            s.add(Consulta(perfil=profile["name"], portal=portal, fecha=now, cantidad_resultados=len(res.listings),
                           completa=res.completa, bloqueada=res.bloqueada, errores="\n".join(res.errores) or None))
            s.commit()
            summary[profile["name"]] = {**stats, "resultados": len(res.listings), "completa": res.completa,
                                        "bloqueada": res.bloqueada, "errores": res.errores}
            log.info("%s/%s: %s", portal, profile["name"], summary[profile["name"]])
    if any(isinstance(v, dict) for v in summary.values()):   # hubo al menos una consulta: etiquetas automáticas
        try:
            with Session_() as s:
                n = tags.aplicar(s, cfg.get("auto_tags"))
                s.commit()
            log.info("etiquetas automáticas: %d publicaciones actualizadas", n)
        except ValueError as e:   # reglas mal escritas: los avisos ya quedaron guardados, sólo se avisa
            log.error("etiquetas automáticas: %s", e)
    return summary
