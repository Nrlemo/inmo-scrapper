"""Backups automáticos de la SQLite: una vez por día, con la API de backup de SQLite (segura con la base en uso),
verificados (integrity_check), comprimidos, rotados (N diarios + N semanales + N mensuales) y, opcionalmente, copiados a
una segunda carpeta (otro disco u otra máquina).

El estado del último backup se guarda en `estado.json` dentro de la carpeta de backups, no en la base: así un backup no
se describe a sí mismo y el estado sobrevive a una restauración.
"""
import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import config

log = logging.getLogger("inmo.backups")
NOMBRE_RE = re.compile(r"^inmo-(\d{8})-(\d{6})\.sqlite\.gz$")
_lock = threading.Lock()


def _dir() -> Path:
    return Path(config.BACKUP_DIR)


def _estado_path() -> Path:
    return _dir() / "estado.json"


def estado() -> dict:
    try:
        return json.loads(_estado_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_estado(e: dict) -> None:
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        tmp = _estado_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_estado_path())
    except OSError:
        log.warning("no se pudo guardar el estado de los backups", exc_info=True)


def fecha_de(nombre: str) -> datetime | None:
    m = NOMBRE_RE.match(nombre)
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def listar(carpeta: Path | str | None = None) -> list[dict]:
    """Backups de la carpeta, del más nuevo al más viejo: [{nombre, fecha, bytes}]."""
    d = Path(carpeta) if carpeta else _dir()
    out = []
    try:
        for p in d.iterdir():
            f = fecha_de(p.name)
            if f and p.is_file():
                out.append({"nombre": p.name, "fecha": f, "bytes": p.stat().st_size})
    except OSError:
        return []
    return sorted(out, key=lambda x: x["fecha"], reverse=True)


def a_conservar(fechas: list[datetime], diarios: int, semanales: int, mensuales: int) -> set[datetime]:
    """Rotación abuelo-padre-hijo: el más nuevo de cada uno de los últimos `diarios` días, `semanales` semanas (ISO) y
    `mensuales` meses con backups. El más nuevo de todos se conserva siempre."""
    fechas = sorted(fechas, reverse=True)
    quedan: set[datetime] = set(fechas[:1])
    for n, clave in ((diarios, lambda f: f.date()), (semanales, lambda f: f.isocalendar()[:2]),
                     (mensuales, lambda f: (f.year, f.month))):
        vistos = []
        for f in fechas:
            k = clave(f)
            if k not in vistos:
                if len(vistos) >= n:
                    break
                vistos.append(k)
                quedan.add(f)
    return quedan


def rotar(carpeta: Path | str) -> list[str]:
    """Borra los backups que no entran en la retención. Devuelve los nombres borrados."""
    todos = listar(carpeta)
    quedan = a_conservar([b["fecha"] for b in todos], config.BACKUP_DIARIOS, config.BACKUP_SEMANALES,
                         config.BACKUP_MENSUALES)
    borrados = []
    for b in todos:
        if b["fecha"] not in quedan:
            try:
                (Path(carpeta) / b["nombre"]).unlink()
                borrados.append(b["nombre"])
            except OSError:
                log.warning("no se pudo borrar %s", b["nombre"], exc_info=True)
    return borrados


def hacer_backup(now: datetime | None = None, motivo: str = "programado") -> dict:
    """Copia verificada y comprimida de la base. Nunca lanza: el resultado (y el error, si hubo) queda en estado.json."""
    now = now or datetime.now()
    with _lock:
        e = estado()
        e.update(ultimo_intento=now.isoformat(timespec="seconds"), motivo=motivo)
        dest = _dir()
        nombre = f"inmo-{now:%Y%m%d-%H%M%S}.sqlite.gz"
        tmp = dest / f".{nombre}.sqlite"
        try:
            dest.mkdir(parents=True, exist_ok=True)
            src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
                ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
                pubs = dst.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0]
            finally:
                dst.close()
                src.close()
            if ok != "ok":
                raise RuntimeError(f"integrity_check: {ok}")
            with open(tmp, "rb") as fi, gzip.open(dest / f".{nombre}", "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(dest / f".{nombre}", dest / nombre)          # aparece completo o no aparece
            tam = (dest / nombre).stat().st_size
            e.update(ok=True, error=None, ultimo_ok=now.isoformat(timespec="seconds"), nombre=nombre, bytes=tam,
                     publicaciones=pubs, borrados=rotar(dest))
            log.info("backup %s (%d KB, %d publicaciones)", nombre, tam // 1024, pubs)
        except Exception as ex:  # noqa: BLE001 - un backup fallido no debe tumbar la web
            e.update(ok=False, error=f"{type(ex).__name__}: {ex}")
            log.exception("backup fallido")
            _guardar_estado(e)
            return e
        finally:
            tmp.unlink(missing_ok=True)
            (dest / f".{nombre}").unlink(missing_ok=True)
        e["extra"] = _copiar_extra(dest / nombre)
        _guardar_estado(e)
        return e


def _copiar_extra(archivo: Path) -> dict | None:
    """Segunda copia (otro disco u otra máquina). Si falla, el backup principal igual queda hecho."""
    if not config.BACKUP_DIR_EXTRA:
        return None
    extra = Path(config.BACKUP_DIR_EXTRA)
    try:
        extra.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archivo, extra / f".{archivo.name}")
        os.replace(extra / f".{archivo.name}", extra / archivo.name)
        rotar(extra)
        return {"ok": True, "carpeta": str(extra)}
    except OSError as ex:
        log.warning("no se pudo copiar el backup a %s: %s", extra, ex)
        return {"ok": False, "carpeta": str(extra), "error": str(ex)}


# ---------- programación diaria ----------
def toca(now: datetime, ultimo_ok: str | None, hora: str) -> bool:
    """¿Corresponde el backup del día? Pasada la hora y sin un backup correcto desde esa hora de hoy."""
    h, m = map(int, hora.split(":"))
    desde = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < desde:
        return False
    return not ultimo_ok or datetime.fromisoformat(ultimo_ok) < desde


def tick(now: datetime | None = None) -> dict | None:
    now = now or datetime.now()
    e = estado()
    ultimo_intento = e.get("ultimo_intento")
    # tras un fallo, reintentar recién a la hora (no en cada tick)
    if ultimo_intento and not e.get("ok") and now - datetime.fromisoformat(ultimo_intento) < timedelta(hours=1):
        return None
    if toca(now, e.get("ultimo_ok"), config.BACKUP_HORA):
        return hacer_backup(now)
    return None


def run_forever(stop: threading.Event, intervalo: float = 60) -> None:
    while not stop.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001 - el programador nunca debe morir
            log.exception("error en el programador de backups")
        stop.wait(intervalo)
