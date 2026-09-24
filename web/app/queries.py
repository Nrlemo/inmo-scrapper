"""Consultas SQL de lectura (SQLite compartida)."""
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

BASE = """
SELECT p.*, COALESCE(r.favorito,0) favorito, COALESCE(r.potencial,0) potencial, COALESCE(r.descartada,0) descartada,
  COALESCE(r.contactada,0) contactada, COALESCE(r.revisada,0) revisada, r.notas, r.modificado_por,
  r.fecha_contacto, pu.promedio puntaje, COALESCE(pu.votos,0) votos, c.etiquetas, c.etiquetas_auto,
  CASE WHEN COALESCE(p.m2_cubiertos, p.m2_totales) > 0 AND p.moneda = 'USD' THEN p.precio / COALESCE(p.m2_cubiertos, p.m2_totales) END usd_m2,
  ch.variacion_pct, ch.fecha cambio_fecha, i.nombre inmo_nombre, vb.pct vb_pct, vb.ref vb_ref, vb.n vb_n
FROM publicaciones p
LEFT JOIN web_revision r ON r.publicacion_id = p.id
LEFT JOIN categorizacion c ON c.publicacion_id = p.id
LEFT JOIN (SELECT publicacion_id, AVG(puntaje) promedio, COUNT(*) votos FROM web_puntajes GROUP BY publicacion_id) pu
  ON pu.publicacion_id = p.id
LEFT JOIN historial_precios ch ON ch.id = (
  SELECT h.id FROM historial_precios h WHERE h.publicacion_id = p.id AND h.variacion_pct IS NOT NULL
  ORDER BY h.fecha DESC, h.id DESC LIMIT 1)
LEFT JOIN inmobiliarias i ON i.id = p.inmobiliaria_id
LEFT JOIN web_vs_barrio vb ON vb.publicacion_id = p.id
"""

# Fuera de los filtros de búsqueda actuales (inmo.filtros): se oculta, salvo que ya la hayas marcado (favorita,
# potencial o contactada): lo que marcaste no desaparece por cambiar los filtros.
VISIBLE = ("(COALESCE(p.fuera_filtro,0) = 0 OR COALESCE(r.favorito,0) = 1 OR COALESCE(r.potencial,0) = 1"
           " OR COALESCE(r.contactada,0) = 1)")

ORDENES = {
    "nuevas": "p.fecha_primera_vista DESC, p.id DESC",
    "precio_asc": "p.precio ASC", "precio_desc": "p.precio DESC",
    "usd_m2": "usd_m2 ASC", "m2": "COALESCE(p.m2_cubiertos, p.m2_totales) DESC",
    "baja": "ch.variacion_pct ASC", "puntaje": "puntaje DESC NULLS LAST, votos DESC",
    "oportunidad": "vb.pct ASC",
}


@dataclass
class Filtros:
    q: str = ""
    barrio: str = ""
    portal: str = ""
    inmo: int | None = None
    pmin: float | None = None
    pmax: float | None = None
    mmin: float | None = None
    amb: int | None = None
    cochera: bool = False
    etiqueta: str = ""     # manual o automática
    estado: str = ""       # pendientes | favoritas | potencial | contactadas | descartadas | activas (=no descartadas)
    baja: bool = False     # bajó de precio
    bajo_barrio: bool = False  # USD/m² debajo de la mediana de su barrio (app/mercado.py)
    nuevas: bool = False   # desde tu última visita
    inactivas: bool = False
    fuera: bool = False    # incluir las que no cumplen los filtros de búsqueda
    orden: str = "nuevas"
    pagina: int = 1
    desde: datetime | None = field(default=None, repr=False)   # visita previa del usuario

    def where(self) -> tuple[str, dict]:
        w, a = [], {}
        if self.q:
            w.append("(p.titulo LIKE :q OR p.direccion LIKE :q OR p.descripcion LIKE :q OR r.notas LIKE :q)")
            a["q"] = f"%{self.q}%"
        if self.barrio:
            w.append("p.barrio = :barrio"); a["barrio"] = self.barrio
        if self.portal:
            w.append("p.portal = :portal"); a["portal"] = self.portal
        if self.inmo:
            w.append("p.inmobiliaria_id = :inmo"); a["inmo"] = self.inmo
        if self.pmin is not None:
            w.append("p.precio >= :pmin"); a["pmin"] = self.pmin
        if self.pmax is not None:
            w.append("p.precio <= :pmax"); a["pmax"] = self.pmax
        if self.mmin is not None:
            w.append("COALESCE(p.m2_cubiertos, p.m2_totales) >= :mmin"); a["mmin"] = self.mmin
        if self.amb:
            w.append("p.ambientes >= :amb"); a["amb"] = self.amb
        if self.cochera:
            w.append("p.cocheras > 0")
        if self.etiqueta:
            w.append("(EXISTS (SELECT 1 FROM json_each(COALESCE(c.etiquetas, '[]')) WHERE value = :et)"
                     " OR EXISTS (SELECT 1 FROM json_each(COALESCE(c.etiquetas_auto, '[]')) WHERE value = :et))")
            a["et"] = self.etiqueta
        estados = {
            "pendientes": "COALESCE(r.revisada,0) = 0 AND COALESCE(r.descartada,0) = 0",
            "favoritas": "r.favorito = 1", "potencial": "r.potencial = 1", "contactadas": "r.contactada = 1",
            "descartadas": "r.descartada = 1", "activas": "COALESCE(r.descartada,0) = 0",
        }
        if self.estado in estados:
            w.append(estados[self.estado])
        if self.baja:
            w.append("ch.variacion_pct < 0")
        if self.bajo_barrio:
            w.append("vb.pct < 0")
        if self.nuevas and self.desde:
            w.append("p.fecha_primera_vista > :desde"); a["desde"] = self.desde
        if not self.inactivas:
            w.append("p.activa = 1")
        if not self.fuera:
            w.append(VISIBLE)
        return (" WHERE " + " AND ".join(w)) if w else "", a


def foto_chica(url: str | None) -> str:
    """Miniatura: las fotos se guardan en 720x532 (galería); para listados alcanza la versión de 360x266 del CDN."""
    return (url or "").replace("/720x532/", "/360x266/")


def _row(m) -> dict:
    d = dict(m)
    d["fotos"] = json.loads(d["fotos"]) if isinstance(d.get("fotos"), str) else (d.get("fotos") or [])
    for k in ("etiquetas", "etiquetas_auto"):
        d[k] = json.loads(d[k]) if isinstance(d.get(k), str) else (d.get(k) or [])
    return d


def listar(c: Connection, f: Filtros, size: int) -> tuple[list[dict], int]:
    where, args = f.where()
    total = c.execute(text(f"SELECT COUNT(*) FROM ({BASE}{where})"), args).scalar_one()
    order = ORDENES.get(f.orden, ORDENES["nuevas"])
    rows = c.execute(text(f"{BASE}{where} ORDER BY {order} NULLS LAST, p.id DESC LIMIT :n OFFSET :o"),
                     {**args, "n": size, "o": (max(f.pagina, 1) - 1) * size}).mappings().all()
    return [_row(r) for r in rows], total


def uno(c: Connection, pid: int) -> dict | None:
    r = c.execute(text(BASE + " WHERE p.id = :id"), {"id": pid}).mappings().first()
    return _row(r) if r else None


def varios(c: Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    ph = ",".join(f":i{k}" for k in range(len(ids)))
    rows = c.execute(text(f"{BASE} WHERE p.id IN ({ph})"), {f"i{k}": v for k, v in enumerate(ids)}).mappings().all()
    by = {r["id"]: _row(r) for r in rows}
    return [by[i] for i in ids if i in by]


def siguiente_pendiente(c: Connection, despues: int | None) -> tuple[dict | None, int]:
    """Cola de revisión: nuevas primero (id desc). `despues` = id ya visto (saltar)."""
    cond = "COALESCE(r.revisada,0)=0 AND COALESCE(r.descartada,0)=0 AND p.activa=1 AND COALESCE(p.fuera_filtro,0)=0"
    total = c.execute(text(f"SELECT COUNT(*) FROM publicaciones p LEFT JOIN web_revision r ON r.publicacion_id=p.id WHERE {cond}")).scalar_one()
    extra = " AND p.id < :d" if despues else ""
    r = c.execute(text(f"{BASE} WHERE {cond}{extra} ORDER BY p.id DESC LIMIT 1"), {"d": despues}).mappings().first()
    if r is None and despues:  # dio la vuelta: volver al principio
        r = c.execute(text(f"{BASE} WHERE {cond} ORDER BY p.id DESC LIMIT 1")).mappings().first()
    return (_row(r) if r else None), total


def historial(c: Connection, pid: int) -> list[dict]:
    rows = c.execute(text("SELECT precio, moneda, fecha, variacion_pct FROM historial_precios "
                          "WHERE publicacion_id=:i ORDER BY fecha, id"), {"i": pid}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["fecha"], str):
            d["fecha"] = datetime.fromisoformat(d["fecha"])
        out.append(d)
    return out


def hermanas(c: Connection, p: dict) -> list[dict]:
    if not p.get("grupo_id"):
        return []
    rows = c.execute(text(BASE + " WHERE p.grupo_id = :g AND p.id != :i"), {"g": p["grupo_id"], "i": p["id"]}).mappings().all()
    return [_row(r) for r in rows]


def eventos(c: Connection, pid: int, n: int = 15) -> list[dict]:
    rows = c.execute(text("SELECT usuario, tipo, detalle, fecha FROM web_eventos WHERE publicacion_id=:i "
                          "ORDER BY id DESC LIMIT :n"), {"i": pid, "n": n}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["fecha"], str):
            d["fecha"] = datetime.fromisoformat(d["fecha"])
        out.append(d)
    return out


def cambios(c: Connection, dias: int, solo_favoritas: bool) -> list[dict]:
    """Cambios de precio recientes (más nuevo primero)."""
    extra = " AND r.favorito = 1" if solo_favoritas else ""
    rows = c.execute(text(f"""
      SELECT p.id, p.titulo, p.direccion, p.barrio, p.url, p.fotos, p.activa, h.precio, h.moneda, h.fecha, h.variacion_pct,
        (SELECT h2.precio FROM historial_precios h2 WHERE h2.publicacion_id = p.id AND h2.id < h.id ORDER BY h2.id DESC LIMIT 1) precio_anterior,
        COALESCE(r.favorito,0) favorito, COALESCE(r.descartada,0) descartada
      FROM historial_precios h JOIN publicaciones p ON p.id = h.publicacion_id
      LEFT JOIN web_revision r ON r.publicacion_id = p.id
      WHERE h.variacion_pct IS NOT NULL AND h.fecha >= datetime('now', :d){extra}
      ORDER BY h.fecha DESC, h.id DESC LIMIT 300"""), {"d": f"-{dias} days"}).mappings().all()
    out = []
    for r in rows:
        d = _row({**r, "etiquetas": None})
        if isinstance(d["fecha"], str):
            d["fecha"] = datetime.fromisoformat(d["fecha"])
        out.append(d)
    return out


def barrios(c: Connection) -> list[str]:
    return [r[0] for r in c.execute(text("SELECT DISTINCT barrio FROM publicaciones WHERE barrio IS NOT NULL AND activa=1 ORDER BY barrio"))]


def etiquetas(c: Connection) -> list[dict]:
    """Etiquetas en uso (manuales y automáticas) en publicaciones activas, con su cantidad."""
    return [dict(r) for r in c.execute(text("""
      SELECT nombre, COUNT(DISTINCT pid) n FROM (
        SELECT c.publicacion_id pid, t.value nombre
        FROM categorizacion c JOIN publicaciones p ON p.id = c.publicacion_id AND p.activa = 1,
             json_each(COALESCE(c.etiquetas, '[]')) t
        UNION ALL
        SELECT c.publicacion_id, t.value
        FROM categorizacion c JOIN publicaciones p ON p.id = c.publicacion_id AND p.activa = 1,
             json_each(COALESCE(c.etiquetas_auto, '[]')) t)
      GROUP BY nombre ORDER BY nombre""")).mappings()]


def portales(c: Connection) -> list[str]:
    return [r[0] for r in c.execute(text("SELECT DISTINCT portal FROM publicaciones ORDER BY portal"))]


def inmobiliarias(c: Connection, n: int = 60) -> list[dict]:
    rows = c.execute(text("SELECT i.id, i.nombre, COUNT(*) n FROM inmobiliarias i JOIN publicaciones p ON p.inmobiliaria_id=i.id "
                          "WHERE p.activa=1 GROUP BY i.id ORDER BY n DESC LIMIT :n"), {"n": n}).mappings().all()
    return [dict(r) for r in rows]


def stats_barrios(c: Connection) -> list[dict]:
    """USD/m² por barrio con las publicaciones activas en USD (sub-barrios agrupados), el mismo cálculo que el badge."""
    from .mercado import estadisticas
    return estadisticas(c)


def mapa(c: Connection, f: Filtros) -> list[dict]:
    where, args = f.where()
    w = (where + " AND " if where else " WHERE ") + "p.lat IS NOT NULL"
    rows = c.execute(text(f"{BASE}{w} LIMIT 3000"), args).mappings().all()
    out = []
    for r in rows:
        d = _row(r)
        out.append({"id": d["id"], "lat": d["lat"], "lng": d["lng"], "precio": d["precio"], "moneda": d["moneda"],
                    "m2": d["m2_cubiertos"], "amb": d["ambientes"], "dir": d["direccion"], "barrio": d["barrio"],
                    "foto": foto_chica((d["fotos"] or [None])[0]), "fav": bool(d["favorito"]), "pot": bool(d["potencial"]), "desc": bool(d["descartada"]),
                    "cont": bool(d["contactada"]), "baja": (d["variacion_pct"] or 0) < 0})
    return out


def consultas(c: Connection) -> list[dict]:
    rows = c.execute(text("SELECT perfil, portal, fecha, cantidad_resultados, completa, bloqueada, errores "
                          "FROM consultas ORDER BY fecha DESC LIMIT 30")).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["fecha"], str):
            d["fecha"] = datetime.fromisoformat(d["fecha"])
        out.append(d)
    return out


def contadores(c: Connection, desde: datetime | None) -> dict:
    q = lambda s, a=None: c.execute(text(s), a or {}).scalar_one()  # noqa: E731
    return {
        "pendientes": q("SELECT COUNT(*) FROM publicaciones p LEFT JOIN web_revision r ON r.publicacion_id=p.id "
                        "WHERE p.activa=1 AND COALESCE(p.fuera_filtro,0)=0 AND COALESCE(r.revisada,0)=0 AND COALESCE(r.descartada,0)=0"),
        "favoritas": q("SELECT COUNT(*) FROM web_revision WHERE favorito=1 AND descartada=0"),
        "potencial": q("SELECT COUNT(*) FROM web_revision WHERE potencial=1 AND descartada=0"),
        "corriendo": q("SELECT COUNT(*) FROM ejecuciones WHERE estado='corriendo'"),
        "nuevas": q("SELECT COUNT(*) FROM publicaciones WHERE activa=1 AND COALESCE(fuera_filtro,0)=0 AND fecha_primera_vista > :d",
                    {"d": desde}) if desde else 0,
    }


def revisadas_hoy(c: Connection, usuario: str) -> int:
    """Publicaciones que `usuario` marcó hoy (favorita, potencial, descartada o contactada): progreso de la cola."""
    hoy = datetime.combine(datetime.now().date(), datetime.min.time())
    return c.execute(text("SELECT COUNT(DISTINCT publicacion_id) FROM web_eventos WHERE usuario=:u AND fecha >= :d "
                          "AND tipo IN ('favorito','potencial','descartar','contactada')"), {"u": usuario, "d": hoy}).scalar_one()


def mi_puntaje(c: Connection, pid: int, usuario: str) -> int | None:
    return c.execute(text("SELECT puntaje FROM web_puntajes WHERE publicacion_id=:i AND usuario=:u"),
                     {"i": pid, "u": usuario}).scalar()


def puntajes(c: Connection, pid: int) -> list[dict]:
    """Puntaje de cada usuario para una publicación."""
    return [dict(r) for r in c.execute(text("SELECT usuario, puntaje FROM web_puntajes WHERE publicacion_id=:i "
                                            "ORDER BY puntaje DESC, usuario"), {"i": pid}).mappings()]


def ranking(c: Connection, usuario: str = "", inactivas: bool = False, descartadas: bool = False,
            solo_cuentas: bool = False) -> tuple[list[dict], list[str]]:
    """Publicaciones puntuadas, de mayor a menor promedio. Con `usuario`, ordena por el puntaje de esa persona.
    Devuelve las filas (cada una con `por_usuario`: {usuario: puntaje}) y los usuarios que puntuaron."""
    w = ["pu.votos > 0"]
    if not inactivas:
        w.append("p.activa = 1")
    if not descartadas:
        w.append("COALESCE(r.descartada,0) = 0")
    args: dict = {}
    order = "puntaje DESC, votos DESC, p.id DESC"
    if usuario:
        w.append("EXISTS (SELECT 1 FROM web_puntajes x WHERE x.publicacion_id = p.id AND x.usuario = :u)")
        order = "(SELECT x.puntaje FROM web_puntajes x WHERE x.publicacion_id = p.id AND x.usuario = :u) DESC, " + order
        args["u"] = usuario
    rows = [_row(r) for r in c.execute(text(f"{BASE} WHERE {' AND '.join(w)} ORDER BY {order} LIMIT 500"), args).mappings()]
    por: dict[int, dict[str, int]] = {}
    for pid, u, v in c.execute(text("SELECT publicacion_id, usuario, puntaje FROM web_puntajes")):
        por.setdefault(pid, {})[u] = v
    for r in rows:
        r["por_usuario"] = por.get(r["id"], {})
    # Con login propio, una columna por cuenta: puntajes de identidades sin cuenta (p. ej. de antes de que existieran
    # las cuentas) siguen en el promedio, pero no generan columna.
    cuentas = " WHERE usuario IN (SELECT username FROM web_cuentas)" if solo_cuentas else ""
    usuarios = [x[0] for x in c.execute(text(f"SELECT usuario FROM web_puntajes{cuentas} GROUP BY usuario "
                                             "ORDER BY COUNT(*) DESC, usuario"))]
    return rows, usuarios
