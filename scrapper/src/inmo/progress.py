"""Seguimiento del progreso de una corrida en la tabla `ejecuciones`. Nunca debe romper el scraping."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .models import Ejecucion

log = logging.getLogger(__name__)


class Tracker:
    def __init__(self, engine, run_id: int):
        self.engine, self.run_id = engine, run_id

    def update(self, **kw) -> None:
        self._write(kw)

    def finish(self, estado: str, mensaje: str | None = None) -> None:
        self._write({"estado": estado, "mensaje": mensaje, "fin": datetime.now()})

    def _write(self, kw: dict) -> None:
        try:
            with Session(self.engine) as s:
                e = s.get(Ejecucion, self.run_id)
                if e is None or (e.estado != "corriendo" and "fin" not in kw):
                    return  # cancelada/interrumpida desde la web: no pisar
                for k, v in kw.items():
                    setattr(e, k, v)
                e.actualizado = datetime.now()
                s.commit()
        except Exception:  # noqa: BLE001
            log.warning("no se pudo registrar el progreso", exc_info=True)
