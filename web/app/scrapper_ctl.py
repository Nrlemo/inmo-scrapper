"""Lanza y vigila corridas del scrapper (subproceso `python -m inmo run`) y expone su progreso."""
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from inmo.config import load_config
from inmo.models import Ejecucion

from . import config

_procs: dict[int, subprocess.Popen] = {}
_lock = threading.Lock()


class Ocupado(Exception):
    """Ya hay una corrida en curso."""


def portales_y_zonas() -> dict[str, list[str]]:
    """{portal: [zonas únicas]} según los perfiles del YAML (portales sin zonas -> lista vacía)."""
    out: dict[str, list[str]] = {}
    try:
        perfiles = load_config(config.CONFIG_PATH)["profiles"]
    except (OSError, ValueError, KeyError):   # sin profiles.yaml (repo recién clonado) o mal formado
        return out
    for p in perfiles:
        for portal, pc in p.get("portals", {}).items():
            zs = out.setdefault(portal, [])
            for z in pc.get("zones") or []:
                name = z["zone"] if isinstance(z, dict) else z
                if name not in zs:
                    zs.append(name)
    return out


def activa(s: Session) -> Ejecucion | None:
    return s.scalar(select(Ejecucion).where(Ejecucion.estado == "corriendo").order_by(Ejecucion.id.desc()).limit(1))


def log_path(run_id: int) -> Path:
    d = Path(config.DB_PATH).parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"run-{run_id}.log"


def cola_log(run_id: int, n: int = 8) -> list[str]:
    try:
        return log_path(run_id).read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def build_cmd(run_id: int, portal: str, zonas: list[str], skip_gap: bool) -> list[str]:
    cmd = [sys.executable, "-m", "inmo", "run", "--portal", portal, "--run-id", str(run_id),
           "--config", config.CONFIG_PATH, "--db", config.DB_PATH]
    if skip_gap:
        cmd.append("--skip-gap")
    for z in zonas:
        cmd += ["--zone", z]
    return cmd


def iniciar(engine, usuario: str, portal: str, zonas: list[str], skip_gap: bool) -> int:
    disponibles = portales_y_zonas()
    if portal not in disponibles:
        raise ValueError("Portal desconocido")
    if any(z not in disponibles[portal] for z in zonas):
        raise ValueError("Zona desconocida")
    zonas = [] if set(zonas) >= set(disponibles[portal]) else zonas  # todas = sin filtro (también inactiva no vistas)
    with _lock, Session(engine) as s:
        if activa(s):
            raise Ocupado()
        now = datetime.now()
        e = Ejecucion(portal=portal, zonas=zonas, usuario=usuario, inicio=now, actualizado=now, estado="corriendo")
        s.add(e)
        s.commit()
        run_id = e.id
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [config._src, os.environ.get("PYTHONPATH")]))}
        with open(log_path(run_id), "wb") as lf:
            proc = subprocess.Popen(build_cmd(run_id, portal, zonas, skip_gap), stdout=lf, stderr=subprocess.STDOUT,
                                    env=env, start_new_session=True)
        e.pid = proc.pid
        s.commit()
        _procs[run_id] = proc
    threading.Thread(target=_vigilar, args=(engine, run_id, proc), daemon=True).start()
    _limpiar_logs()
    return run_id


def _vigilar(engine, run_id: int, proc: subprocess.Popen) -> None:
    rc = proc.wait()
    _procs.pop(run_id, None)
    with Session(engine) as s:
        e = s.get(Ejecucion, run_id)
        if e and e.estado == "corriendo":  # murió sin cerrar su registro
            e.estado, e.fin = "interrumpida", datetime.now()
            e.mensaje = f"El proceso terminó inesperadamente (código {rc}). Ver el log."
            s.commit()


def cancelar(engine, run_id: int) -> None:
    # Primero se marca: si no, el vigilante vería morir el proceso y la registraría como "interrumpida".
    with Session(engine) as s:
        e = s.get(Ejecucion, run_id)
        if e and e.estado == "corriendo":
            e.estado, e.fin, e.mensaje = "cancelada", datetime.now(), "Cancelada manualmente (no se guardó ningún aviso de esta corrida)."
            s.commit()
    proc = _procs.get(run_id)
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def marcar_huerfanas(engine) -> None:
    """Al arrancar la web no puede haber corridas en curso: las que figuran así quedaron cortadas."""
    with Session(engine) as s:
        s.execute(update(Ejecucion).where(Ejecucion.estado == "corriendo").values(
            estado="interrumpida", fin=datetime.now(), mensaje="Interrumpida por reinicio del servicio."))
        s.commit()


def _limpiar_logs(keep: int = 30) -> None:
    logs = sorted(log_path(0).parent.glob("run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in logs[keep:]:
        p.unlink(missing_ok=True)


def vista(e: Ejecucion) -> dict:
    """Ejecucion -> dict para plantillas, con % de avance y tiempo transcurrido."""
    pct = None
    if e.estado == "corriendo" and e.zonas_total:
        frac = (max(e.zona_idx - 1, 0) + (e.pagina / e.paginas if e.paginas else 0)) / e.zonas_total
        pct = min(int(frac * 100), 99)
    fin = e.fin or datetime.now()
    return {"id": e.id, "portal": e.portal, "zonas": e.zonas or [], "usuario": e.usuario, "estado": e.estado,
            "inicio": e.inicio, "fin": e.fin, "minutos": int((fin - e.inicio).total_seconds() // 60),
            "zonas_total": e.zonas_total, "zona_idx": e.zona_idx, "zona_actual": e.zona_actual,
            "pagina": e.pagina, "paginas": e.paginas, "resultados": e.resultados, "mensaje": e.mensaje, "pct": pct}
