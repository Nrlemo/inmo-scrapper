"""Programador diario: lanza el scrapper de madrugada si el usuario lo activó (pantalla Estado).

Hilo liviano dentro de la web (sin dependencias extra). El horario es la hora elegida más una demora
aleatoria de hasta JITTER_MIN minutos, distinta cada día, para no golpear el portal siempre a la misma hora.
"""
import logging
import random
import re
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import scrapper_ctl
from .models_web import Programacion

log = logging.getLogger(__name__)
JITTER_MIN = 30      # demora aleatoria máxima sobre la hora elegida
GRACIA_H = 3         # si la web estuvo caída y se pasó la hora por más de esto, se salta a mañana
HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_cola: list[str] = []   # portales pendientes de lanzar (de a uno: sólo hay una corrida a la vez)


def calcular_proxima(hora: str, now: datetime, rnd=random.random) -> datetime:
    h, m = map(int, hora.split(":"))
    base = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while True:
        prox = base + timedelta(minutes=int(rnd() * JITTER_MIN))
        if prox > now:
            return prox
        base += timedelta(days=1)


def obtener(s: Session) -> Programacion:
    p = s.get(Programacion, 1)
    if p is None:
        p = Programacion(id=1, activa=False, hora="03:00")
        s.add(p)
        s.commit()
    return p


def configurar(s: Session, activa: bool, hora: str, usuario: str, now: datetime | None = None) -> Programacion:
    if not HORA_RE.match(hora):
        raise ValueError("Hora inválida (usar HH:MM, por ejemplo 03:00)")
    p = obtener(s)
    p.activa, p.hora, p.modificado_por = activa, hora, usuario
    p.proxima = calcular_proxima(hora, now or datetime.now()) if activa else None
    s.commit()
    if not activa:
        _cola.clear()
    return p


def tick(engine, now: datetime | None = None, iniciar=scrapper_ctl.iniciar) -> None:
    """Se llama periódicamente: dispara la corrida diaria si llegó la hora y avanza la cola de portales."""
    now = now or datetime.now()
    with Session(engine) as s:
        p = obtener(s)
        if p.activa and p.proxima and now >= p.proxima:
            if now - p.proxima <= timedelta(hours=GRACIA_H):
                _cola[:] = list(scrapper_ctl.portales_y_zonas())
                p.ultima_auto = now
            else:
                log.warning("Corrida programada omitida: la hora %s ya pasó por más de %s h", p.proxima, GRACIA_H)
            # siempre para un día posterior: con la demora aleatoria, la hora de hoy podría caer unos minutos después
            p.proxima = calcular_proxima(p.hora, now.replace(hour=23, minute=59, second=59))
            s.commit()
    if _cola:
        try:
            iniciar(engine, "programada", _cola[0], [], False)
            _cola.pop(0)
        except scrapper_ctl.Ocupado:
            pass  # hay otra corrida en curso: se reintenta en el próximo tick
        except Exception:  # noqa: BLE001
            log.exception("No se pudo lanzar la corrida programada de %s", _cola[0])
            _cola.pop(0)


def run_forever(engine, stop: threading.Event, interval: float = 30) -> None:
    while not stop.is_set():
        try:
            tick(engine)
        except Exception:  # noqa: BLE001 - el programador nunca debe morir
            log.exception("Error en el programador")
        stop.wait(interval)
