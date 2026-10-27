"""Consultas SQL de lectura (SQLite compartida)."""
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Precio por m² (sobre los cubiertos o, si no hay, los totales) y costo mensual de un alquiler (precio + expensas, sólo si
# las expensas están en la misma moneda que el precio). Se usan en las columnas, los filtros y los órdenes.
PRECIO_M2 = "CASE WHEN COALESCE(p.m2_cubiertos, p.m2_totales) > 0 THEN p.precio / COALESCE(p.m2_cubiertos, p.m2_totales) END"
COSTO_MES = "(p.precio + COALESCE(CASE WHEN p.moneda_expensas IS NULL OR p.moneda_expensas = p.moneda THEN p.expensas END, 0))"

BASE = """
SELECT p.*, COALESCE(r.favorito,0) favorito, COALESCE(r.potencial,0) potencial, COALESCE(r.descartada,0) descartada,
  COALESCE(r.contactada,0) contactada, COALESCE(r.revisada,0) revisada, r.notas, r.modificado_por,
  r.fecha_contacto, pu.promedio puntaje, COALESCE(pu.votos,0) votos, c.etiquetas, c.etiquetas_auto,
  {PRECIO_M2} usd_m2,
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
""".replace("{PRECIO_M2}", PRECIO_M2)

# Para contar resultados no hacen falta el promedio de puntajes ni el último cambio de precio por fila: sólo las tablas
# que puede usar Filtros.where() (el último cambio, sólo con el filtro «bajó de precio»).
_CONTEO = """SELECT COUNT(*) FROM publicaciones p
LEFT JOIN web_revision r ON r.publicacion_id = p.id
LEFT JOIN categorizacion c ON c.publicacion_id = p.id
LEFT JOIN web_vs_barrio vb ON vb.publicacion_id = p.id"""
_JOIN_CAMBIO = """
LEFT JOIN historial_precios ch ON ch.id = (
  SELECT h.id FROM historial_precios h WHERE h.publicacion_id = p.id AND h.variacion_pct IS NOT NULL
  ORDER BY h.fecha DESC, h.id DESC LIMIT 1)"""

# Fuera de los filtros de búsqueda actuales (inmo.filtros): se oculta, salvo que ya la hayas marcado (favorita,
# potencial o contactada): lo que marcaste no desaparece por cambiar los filtros.
VISIBLE = ("(COALESCE(p.fuera_filtro,0) = 0 OR COALESCE(r.favorito,0) = 1 OR COALESCE(r.potencial,0) = 1"
           " OR COALESCE(r.contactada,0) = 1)")

ORDENES = {
    "nuevas": "p.fecha_primera_vista DESC, p.id DESC",
    "precio_asc": "p.precio ASC", "precio_desc": "p.precio DESC",
    "usd_m2": "usd_m2 ASC", "expensas_asc": "p.expensas ASC", "expensas_desc": "p.expensas DESC", "costo_asc": COSTO_MES + " ASC", "m2": "COALESCE(p.m2_cubiertos, p.m2_totales) DESC",
    "baja": "ch.variacion_pct ASC", "puntaje": "puntaje DESC NULLS LAST, votos DESC",
    "oportunidad": "vb.pct ASC",
}


_TIENE_ET = ("EXISTS (SELECT 1 FROM json_each(COALESCE(c.etiquetas, '[]')) WHERE value = {k})"
             " OR EXISTS (SELECT 1 FROM json_each(COALESCE(c.etiquetas_auto, '[]')) WHERE value = {k})")


def _lista(v) -> list[str]:
    """Un valor suelto o una lista, como lista sin vacíos (los enlaces viejos traen `barrio=X`)."""
    return [x for x in ([v] if isinstance(v, str) else (v or [])) if x]


def _marcas(prefijo: str, valores: list[str], args: dict) -> str:
    """Parámetros nombrados para un IN (...): agrega a `args` y devuelve «:b0, :b1…»."""
    for i, v in enumerate(valores):
        args[f"{prefijo}{i}"] = v
    return ", ".join(f":{prefijo}{i}" for i in range(len(valores)))


@dataclass
class Filtros:
    q: str = ""
    barrio: str | list[str] = ""      # uno o varios (cualquiera de ellos)
    no_barrio: list[str] = field(default_factory=list)
    portal: str = ""
    moneda: str = ""       # ARS | USD: los precios sólo se comparan dentro de una moneda
    inmo: int | None = None
    pmin: float | None = None
    pmax: float | None = None
    mmin: float | None = None
    mmax: float | None = None   # m² cubiertos máx
    tmax: float | None = None   # m² totales máx
    emin: float | None = None   # expensas (tal como las informa el aviso)
    emax: float | None = None
    pm2min: float | None = None  # precio por m²
    pm2max: float | None = None
    cmmin: float | None = None  # costo mensual (precio + expensas), alquiler
    cmmax: float | None = None
    amb: int | None = None
    amax: int | None = None
    dmin: int | None = None
    dmax: int | None = None
    bmin: int | None = None
    cmin: int | None = None     # cocheras mín
    cochera: bool = False
    etiqueta: str | list[str] = ""   # manual o automática; varias = todas
    no_etiqueta: list[str] = field(default_factory=list)
    sin_etiquetas: bool = False
    sin_notas: bool = False
    sin_dup: bool = False   # un solo aviso por inmueble repetido en varios portales
    estado: str = ""       # pendientes | favoritas | potencial | contactadas | descartadas | activas (=no descartadas)
    baja: bool = False     # bajó de precio
    bajo_barrio: bool = False  # precio/m² debajo de la mediana de su barrio (app/mercado.py)
    nuevas: bool = False   # desde tu última visita
    inactivas: bool = False
    fuera: bool = False    # incluir las que no cumplen los filtros de búsqueda
    orden: str = "nuevas"
    pagina: int = 1
    desde: datetime | None = field(default=None, repr=False)   # visita previa del usuario

    # Filtros que cuentan para decidir si el panel se muestra abierto (no son búsqueda de texto, orden ni página).
    _PANEL = ("barrio", "no_barrio", "portal", "moneda", "inmo", "pmin", "pmax", "mmin", "mmax", "tmax", "emin", "emax", "pm2min",
              "pm2max", "cmmin", "cmmax", "amb", "amax", "dmin", "dmax", "bmin", "cmin", "cochera", "etiqueta", "no_etiqueta",
              "sin_etiquetas", "sin_notas", "sin_dup", "estado", "baja", "bajo_barrio", "nuevas", "inactivas", "fuera")

    @property
    def hay_filtros(self) -> bool:
        return any(getattr(self, k) not in (None, "", False, []) for k in self._PANEL)

    @property
    def hay_mas(self) -> bool:
        """Hay algún filtro fuera de los «esenciales» (siempre visibles): el panel «Más filtros» se muestra abierto."""
        esenciales = ("estado", "barrio", "pmin", "pmax", "mmin", "mmax")
        return any(getattr(self, k) not in (None, "", False, []) for k in self._PANEL if k not in esenciales)

    @property
    def barrios(self) -> list[str]:
        return _lista(self.barrio)

    @property
    def etiquetas(self) -> list[str]:
        return _lista(self.etiqueta)

    def where(self) -> tuple[str, dict]:
        w, a = [], {}
        if self.q:
            w.append("(p.titulo LIKE :q OR p.direccion LIKE :q OR p.descripcion LIKE :q OR r.notas LIKE :q)")
            a["q"] = f"%{self.q}%"
        if self.barrios:
            ph = _marcas("b", self.barrios, a)
            w.append(f"p.barrio IN ({ph})")
        if self.no_barrio:
            ph = _marcas("nb", self.no_barrio, a)
            w.append(f"COALESCE(p.barrio, '') NOT IN ({ph})")
        if self.portal:
            w.append("p.portal = :portal"); a["portal"] = self.portal
        if self.moneda in ("ARS", "USD"):
            w.append("p.moneda = :moneda"); a["moneda"] = self.moneda
        if self.inmo:
            w.append("p.inmobiliaria_id = :inmo"); a["inmo"] = self.inmo
        if self.pmin is not None:
            w.append("p.precio >= :pmin"); a["pmin"] = self.pmin
        if self.pmax is not None:
            w.append("p.precio <= :pmax"); a["pmax"] = self.pmax
        if self.mmin is not None:
            w.append("COALESCE(p.m2_cubiertos, p.m2_totales) >= :mmin"); a["mmin"] = self.mmin
        if self.mmax is not None:
            w.append("p.m2_cubiertos <= :mmax"); a["mmax"] = self.mmax
        if self.tmax is not None:
            w.append("p.m2_totales <= :tmax"); a["tmax"] = self.tmax
        for campo, expr, op in (("emin", "p.expensas", ">="), ("emax", "p.expensas", "<="),
                                ("pm2min", PRECIO_M2, ">="), ("pm2max", PRECIO_M2, "<="),
                                ("cmmin", COSTO_MES, ">="), ("cmmax", COSTO_MES, "<="),
                                ("amax", "p.ambientes", "<="), ("dmin", "p.dormitorios", ">="), ("dmax", "p.dormitorios", "<="),
                                ("bmin", "p.banos", ">="), ("cmin", "p.cocheras", ">=")):
            if getattr(self, campo) is not None:
                w.append(f"{expr} {op} :{campo}"); a[campo] = getattr(self, campo)
        if self.amb:
            w.append("p.ambientes >= :amb"); a["amb"] = self.amb
        if self.cochera:
            w.append("p.cocheras > 0")
        for i, et in enumerate(self.etiquetas):         # varias: tiene que tener todas
            w.append(f"({_TIENE_ET.format(k=f':et{i}')})"); a[f"et{i}"] = et
        for i, et in enumerate(self.no_etiqueta):
            w.append(f"NOT ({_TIENE_ET.format(k=f':ne{i}')})"); a[f"ne{i}"] = et
        if self.sin_etiquetas:
            w.append("(json_array_length(COALESCE(c.etiquetas, '[]')) = 0 AND json_array_length(COALESCE(c.etiquetas_auto, '[]')) = 0)")
        if self.sin_notas:
            w.append("COALESCE(r.notas, '') = ''")
        if self.sin_dup:
            w.append("(p.grupo_id IS NULL OR p.id = (SELECT MIN(q.id) FROM publicaciones q WHERE q.grupo_id = p.grupo_id AND q.activa = 1))")
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
    """Miniatura para listados: cada portal sabe achicar las fotos de su CDN (connectors.miniatura)."""
    from inmo.connectors import miniatura
    return miniatura(url)


def _row(m) -> dict:
    d = dict(m)
    d["fotos"] = json.loads(d["fotos"]) if isinstance(d.get("fotos"), str) else (d.get("fotos") or [])
    for k in ("etiquetas", "etiquetas_auto"):
        d[k] = json.loads(d[k]) if isinstance(d.get(k), str) else (d.get(k) or [])
    return d


def listar(c: Connection, f: Filtros, size: int) -> tuple[list[dict], int]:
    where, args = f.where()
    total = c.execute(text(_CONTEO + (_JOIN_CAMBIO if f.baja else "") + where), args).scalar_one()
    order = ORDENES.get(f.orden, ORDENES["nuevas"])
    rows = c.execute(text(f"{BASE}{where} ORDER BY {order} NULLS LAST, p.id DESC LIMIT :n OFFSET :o"),
                     {**args, "n": size, "o": (max(f.pagina, 1) - 1) * size}).mappings().all()
    return [_row(r) for r in rows], total


def ids_filtrados(c: Connection, f: Filtros, tope: int) -> list[int]:
    """Ids de todo lo que devuelve el listado con estos filtros (sin paginar), para acciones en masa. Los ya
    descartados quedan afuera. Usa el mismo where() y las mismas tablas que el conteo, así coincide con «N resultados»."""
    where, args = f.where()
    extra = "COALESCE(r.descartada,0) = 0"
    where = f"{where} AND {extra}" if where else f" WHERE {extra}"
    sql = _CONTEO.replace("COUNT(*)", "p.id", 1) + (_JOIN_CAMBIO if f.baja else "") + where + " ORDER BY p.id LIMIT :tope"
    return [r[0] for r in c.execute(text(sql), {**args, "tope": tope})]


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


def stats_barrios(c: Connection, base: str = "compra") -> list[dict]:
    """Precio/m² por barrio con las publicaciones activas en la moneda de referencia de la base (sub-barrios agrupados),
    el mismo cálculo que el badge."""
    from .mercado import estadisticas
    return estadisticas(c, base)


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
                    "cont": bool(d["contactada"]), "baja": (d["variacion_pct"] or 0) < 0,
                    "aprox": d.get("geo_fuente") == "direccion"})
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
            cuentas: list[str] | None = None) -> tuple[list[dict], list[str]]:
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
    # `cuentas` viene de la base de autenticación (otra SQLite): no se puede cruzar con un JOIN
    puntuaron = [(x[0], x[1]) for x in c.execute(text("SELECT usuario, COUNT(*) FROM web_puntajes GROUP BY usuario"))]
    usuarios = [u for u, _ in sorted(puntuaron, key=lambda t: (-t[1], t[0])) if cuentas is None or u in cuentas]
    return rows, usuarios
