"""Rondas de carga (las conduce la extensión del navegador, ver inmo.navegador): progreso y control desde la web."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from inmo import filtros, navegador
from inmo.models import Ejecucion, RondaNavegador

from . import busqueda


def portales_y_zonas(s: Session) -> dict[str, list[str]]:
    """{portal: [zonas únicas]} de los filtros de búsqueda (portales activos sin zonas -> lista vacía)."""
    out: dict[str, list[str]] = {}
    for p in filtros.perfiles(busqueda.obtener(s)):
        for portal, pc in p.get("portals", {}).items():
            zs = out.setdefault(portal, [])
            for z in pc.get("zones") or []:
                name = z["zone"] if isinstance(z, dict) else z
                if name not in zs:
                    zs.append(name)
    return out


def activa(s: Session) -> Ejecucion | None:
    return s.scalar(select(Ejecucion).where(Ejecucion.estado == "corriendo").order_by(Ejecucion.id.desc()).limit(1))


def cancelar(s: Session, run_id: int) -> None:
    """Cancela la ronda desde la web: lo recibido queda guardado, pero no se registra consulta ni se dan bajas.
    La extensión se entera en su próximo envío (el servidor le responde «fin»)."""
    navegador.cancelar(s, run_id)


def marcar_huerfanas(s: Session) -> None:
    """Al arrancar: las corridas «corriendo» sin estado de ronda por navegador son del scrapper HTTP (ya no existe) y
    quedaron cortadas. Las rondas por navegador sobreviven al reinicio: la extensión sigue mandando páginas, y si deja
    de hacerlo navegador.iniciar las da por interrumpidas a los 30 min."""
    for e in s.scalars(select(Ejecucion).where(Ejecucion.estado == "corriendo")):
        if s.get(RondaNavegador, e.id) is None:
            e.estado, e.fin, e.mensaje = "interrumpida", datetime.now(), "Interrumpida por reinicio del servicio."
    s.commit()


def vista(e: Ejecucion) -> dict:
    """Ejecucion -> dict para plantillas, con % de avance y tiempo transcurrido."""
    pct = None
    if e.estado == "corriendo" and e.zonas_total:
        frac = (max(e.zona_idx - 1, 0) + (e.pagina / e.paginas if e.paginas else 0)) / e.zonas_total
        pct = min(int(frac * 100), 99)
    fin = e.fin or datetime.now()
    return {"id": e.id, "portal": (e.portal or "").replace(",", ", "), "zonas": e.zonas or [], "usuario": e.usuario, "estado": e.estado,
            "inicio": e.inicio, "fin": e.fin, "minutos": int((fin - e.inicio).total_seconds() // 60),
            "zonas_total": e.zonas_total, "zona_idx": e.zona_idx, "zona_actual": e.zona_actual,
            "pagina": e.pagina, "paginas": e.paginas, "resultados": e.resultados, "mensaje": e.mensaje, "pct": pct}
