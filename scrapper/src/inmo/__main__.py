from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .config import DEFAULT_CONFIG, DEFAULT_DB, load_config
from .models import make_engine
from .progress import Tracker
from .runner import run
from . import tags


def main() -> None:
    ap = argparse.ArgumentParser(prog="inmo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ejecuta una consulta")
    r.add_argument("--portal", required=True)
    r.add_argument("--profile")
    r.add_argument("--force", action="store_true", help="ignora cooldown e intervalo mínimo (usar con cuidado)")
    r.add_argument("--zone", action="append", help="solo esta zona (repetible); no inactiva avisos de las demás")
    r.add_argument("--skip-gap", action="store_true", help="ignora sólo el intervalo mínimo entre corridas (no el cooldown por bloqueo)")
    r.add_argument("--run-id", type=int, help="id de la tabla ejecuciones a la que informar el progreso")
    r.add_argument("--config", default=str(DEFAULT_CONFIG))
    r.add_argument("--db", default=DEFAULT_DB)
    t = sub.add_parser("retag", help="recalcula las etiquetas automáticas (auto_tags del YAML) de todo lo guardado")
    t.add_argument("--config", default=str(DEFAULT_CONFIG))
    t.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(args.db)
    if args.cmd == "retag":
        with Session(engine) as s:
            n = tags.aplicar(s, load_config(args.config).get("auto_tags"))
            s.commit()
        print(f"{n} publicaciones con etiquetas automáticas actualizadas")
        return
    tracker = Tracker(engine, args.run_id) if args.run_id else None
    try:
        out = run(engine, load_config(args.config), args.portal, args.profile, args.force, args.zone,
                  tracker=tracker, skip_gap=args.skip_gap)
    except (SystemExit, Exception) as e:  # noqa: BLE001
        if tracker:
            tracker.finish("error", str(e.code) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}")
        raise
    if tracker:
        tracker.finish(*_resumen(out))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


def _resumen(out: dict) -> tuple[str, str]:
    """(estado, mensaje) de una corrida a partir del resumen del runner."""
    res = [v for v in out.values() if isinstance(v, dict)]
    lineas = [v for v in out.values() if isinstance(v, str)]
    for v in res:
        lineas.append(f"{v['resultados']} avisos: {v['nuevas']} nuevos, {v['precio_cambiado']} con cambio de precio, "
                      f"{v['desactivadas']} dados de baja")
        lineas += v["errores"]
    estado = ("omitida" if not res else "bloqueada" if any(v["bloqueada"] for v in res)
              else "parcial" if not all(v["completa"] for v in res) else "ok")
    return estado, "\n".join(lineas)


if __name__ == "__main__":
    main()
