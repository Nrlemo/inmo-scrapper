"""Indicador de oportunidad: precio por m² de cada aviso frente a la mediana de su barrio (y de su cantidad de
ambientes, si hay muestra suficiente), con las publicaciones activas en la moneda de referencia de la base: USD en
compra y pesos en alquiler (precio mensual por m²).

Los porcentajes se guardan en `web_vs_barrio` (una fila por aviso) y se recalculan sólo cuando cambian los datos
(avisos nuevos o vistos de nuevo, cambios de precio, bajas): así el listado puede filtrar y ordenar en SQL sin
calcular medianas en cada pedido.
"""
import statistics
import threading

from sqlalchemy import text
from sqlalchemy.engine import Connection

MIN_MUESTRA = 5          # publicaciones mínimas para que una mediana sea referencia
# Moneda de referencia y rango de precio/m² de cada base. Fuera del rango es un error de carga en el portal (p. ej. m² de
# más: un depto de 5.400 m²), no una oportunidad: no entra en la muestra ni lleva badge. En alquiler, con la inflación,
# un tope en pesos quedaría viejo enseguida: no se acota (la mediana es robusta a los valores raros).
REFERENCIA = {"compra": ("USD", 300, 15000), "alquiler": ("ARS", 1, 10**9)}
_lock = threading.Lock()
_firma: dict[str, tuple] = {}      # por base


def _params(base: str) -> dict:
    moneda, lo, hi = REFERENCIA[base]
    return {"m": moneda, "lo": lo, "hi": hi}

_FIRMA_SQL = text("""SELECT COUNT(*), SUM(activa), MAX(fecha_ultima_vista), SUM(COALESCE(precio,0)),
                            (SELECT MAX(id) FROM historial_precios) FROM publicaciones""")
_MUESTRA_SQL = text("""SELECT id, barrio, ambientes, precio / COALESCE(m2_cubiertos, m2_totales) v FROM publicaciones
                       WHERE activa = 1 AND moneda = :m AND precio > 0 AND COALESCE(m2_cubiertos, m2_totales) > 0
                         AND barrio IS NOT NULL AND barrio != ''
                         AND precio / COALESCE(m2_cubiertos, m2_totales) BETWEEN :lo AND :hi""")


def grupos(barrios: list[str]) -> dict[str, str]:
    """Sub-barrio -> barrio: «Almagro Norte» y «Almagro Sur» cuentan como «Almagro» si ese barrio aparece;
    «Palermo Soho» como «Palermo». Los portales nombran distinto (y de a pocos) a los sub-barrios."""
    nombres = sorted(set(barrios), key=len)
    out = {}
    for b in nombres:
        padre = next((p for p in nombres if len(p) < len(b) and b.lower().startswith(p.lower() + " ")), None)
        out[b] = out.get(padre, padre) if padre else b
    return out


def calcular(filas: list[tuple]) -> tuple[dict[int, tuple[float, str, int]], list[dict]]:
    """filas: (id, barrio, ambientes, precio_m2). Devuelve ({id: (pct, referencia, n)}, estadísticas por barrio)."""
    g = grupos([f[1] for f in filas])
    por_barrio: dict[str, list[float]] = {}
    por_amb: dict[tuple[str, int], list[float]] = {}
    for _, b, amb, v in filas:
        por_barrio.setdefault(g[b], []).append(v)
        if amb:
            por_amb.setdefault((g[b], amb), []).append(v)
    med_b = {k: statistics.median(v) for k, v in por_barrio.items() if len(v) >= MIN_MUESTRA}
    med_a = {k: statistics.median(v) for k, v in por_amb.items() if len(v) >= MIN_MUESTRA}
    out = {}
    for pid, b, amb, v in filas:
        gb = g[b]
        if (gb, amb) in med_a:
            med, ref, n = med_a[(gb, amb)], f"{gb} {amb} amb.", len(por_amb[(gb, amb)])
        elif gb in med_b:
            med, ref, n = med_b[gb], gb, len(por_barrio[gb])
        else:
            continue                                   # muestra chica: sin referencia
        out[pid] = (round((v / med - 1) * 100, 1), ref, n)
    stats = [{"barrio": b, "n": len(v), "mediana": statistics.median(v), "min": min(v), "max": max(v)}
             for b, v in por_barrio.items()]
    return out, sorted(stats, key=lambda x: x["mediana"])


def actualizar(c: Connection, forzar: bool = False, base: str = "compra") -> bool:
    """Recalcula web_vs_barrio si cambiaron los datos de esa base. Devuelve True si recalculó. Barato cuando no hay
    cambios (una consulta de agregados)."""
    firma = tuple(c.execute(_FIRMA_SQL).one())
    if firma == _firma.get(base) and not forzar:
        return False
    with _lock:
        if firma == _firma.get(base) and not forzar:
            return False
        vs, _ = calcular([tuple(r) for r in c.execute(_MUESTRA_SQL, _params(base))])
        with c.engine.begin() as w:               # transacción propia: no depende del estado de la del pedido
            w.execute(text("DELETE FROM web_vs_barrio"))
            if vs:
                w.execute(text("INSERT INTO web_vs_barrio (publicacion_id, pct, ref, n) VALUES (:i, :p, :r, :n)"),
                          [{"i": i, "p": p, "r": r, "n": n} for i, (p, r, n) in vs.items()])
        _firma[base] = firma
        return True


def estadisticas(c: Connection, base: str = "compra") -> list[dict]:
    """Precio/m² por barrio (sub-barrios agrupados), para la pantalla Comparar."""
    return calcular([tuple(r) for r in c.execute(_MUESTRA_SQL, _params(base))])[1]
