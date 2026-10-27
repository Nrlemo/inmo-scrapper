"""Tareas de mantenimiento por línea de comandos. Los avisos se cargan sólo con la ronda por navegador (la extensión)."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .config import DEFAULT_CONFIG, DEFAULT_DB, load_config
from .models import make_engine
from . import tags


def main() -> None:
    ap = argparse.ArgumentParser(prog="inmo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("retag", help="recalcula las etiquetas automáticas (auto_tags del YAML) de todo lo guardado")
    t.add_argument("--config", default=str(DEFAULT_CONFIG))
    t.add_argument("--db", default=DEFAULT_DB)
    t.add_argument("--operacion", choices=["compra", "alquiler"], default="compra",
                   help="base que se recalcula: suma las auto_tags_<operacion> a las comunes")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(args.db)
    with Session(engine) as s:
        cfg = load_config(args.config)
        reglas = {**(cfg.get("auto_tags") or {}), **(cfg.get(f"auto_tags_{args.operacion}") or {})}
        n = tags.aplicar(s, reglas)
        s.commit()
    print(f"{n} publicaciones con etiquetas automáticas actualizadas")


if __name__ == "__main__":
    main()
