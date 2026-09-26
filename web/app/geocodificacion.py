"""Coordenadas para los avisos que el portal no geolocaliza (Argenprop y MercadoLibre no las publican en el listado).

1. Si el mismo inmueble está en otro portal que sí las publicó (grupo_id), se copian (geo_fuente = «duplicado»).
2. Si no, se geocodifica la dirección con el normalizador de direcciones de la Ciudad de Buenos Aires (USIG):
   geo_fuente = «direccion». Es aproximado: los portales suelen dar la altura de la cuadra («Perón al 1200»).
   Sólo CABA. Si hay varios candidatos en la Ciudad (p. ej. «San Juan 2300»: la avenida y San Juan Bautista de La
   Salle), se elige el más cercano a los avisos de ese barrio que tienen coordenadas del portal; si igual queda a más
   de MAX_KM de ellos, se descarta.

Cada dirección se consulta una sola vez (web_geocache, también las no encontradas). Corre de a tandas en un hilo de
la web, con pausa entre pedidos: nunca pide nada a los portales.
"""
import json
import logging
import statistics
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import config  # noqa: F401 - primero: agrega el paquete inmo (carpeta scrapper/) al path
from .models_web import Geocache
from inmo.models import Publicacion

log = logging.getLogger("inmo.geo")
USIG = "https://servicios.usig.buenosaires.gob.ar/normalizar/"
UA = "inmo (monitor personal de avisos; https://github.com/Nrlemo/inmo-scrapper)"
PAUSA = 0.5                                        # segundos entre pedidos al geocodificador
CABA = (-34.71, -34.52, -58.54, -58.33)            # lat mín/máx, lng mín/máx
MAX_KM = 3.0                                       # un punto más lejos que esto de su barrio es otra calle
REINTENTO = timedelta(days=30)                     # una dirección no encontrada se vuelve a probar después de esto


def normalizar(direccion: str | None) -> str | None:
    """«Avenida Hipólito Yrigoyen 3800, Piso 3» -> «avenida hipolito yrigoyen 3800»; «Peron Al 1200» -> «peron 1200».
    None si no hay calle y altura (no se puede geocodificar)."""
    if not direccion:
        return None
    t = direccion.split(",")[0]                                          # lo que sigue suele ser piso/depto/barrio
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\b(piso|dto|depto|departamento|pb|uf|unidad)\b.*$", "", t)
    t = re.sub(r"\bal\b", " ", t)                                        # «Perón al 1200» -> «Perón 1200»
    t = re.sub(r"(\d+)\s+\d+\s*[°º].*$", r"\1", t)                       # «1600 1°» (piso) -> «1600»
    t = " ".join(re.sub(r"[^a-z0-9. ]", " ", t).split())                 # «Humberto 1º» -> «humberto 1»
    m = re.match(r"^(.+)\s(\d{1,5})$", t)                                  # la altura es el último número
    if not m or not re.search(r"[a-z]", m.group(1)):
        return None
    return f"{m.group(1).strip()} {int(m.group(2)) or 1}"               # «al 0»: USIG no acepta 0, sí 1 (misma cuadra)


def en_caba(lat: float, lng: float) -> bool:
    return CABA[0] <= lat <= CABA[1] and CABA[2] <= lng <= CABA[3]


def usig(direccion: str) -> list[tuple[float, float, str]]:
    """Candidatos en CABA [(lat, lng, dirección normalizada)], en el orden de USIG."""
    url = USIG + "?" + urllib.parse.urlencode({"direccion": direccion, "geocodificar": "TRUE", "srid": 4326})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        datos = json.loads(r.read().decode("utf-8"))
    out = []
    for d in datos.get("direccionesNormalizadas") or []:
        c = d.get("coordenadas") or {}
        if d.get("nombre_partido") != "CABA" or not c.get("x") or not c.get("y"):
            continue
        lat, lng = float(c["y"]), float(c["x"])
        if en_caba(lat, lng):
            out.append((lat, lng, str(d.get("direccion") or "")[:200]))
    return out


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + ((a[1] - b[1]) * 0.82) ** 2) ** 0.5 * 111      # 0,82 ≈ cos(34,6°)


def elegir(candidatos: list[tuple[float, float, str]], centro: tuple[float, float] | None):
    """El candidato más cercano al centro del barrio (o el primero si no hay centro). None si queda lejos."""
    if not candidatos:
        return None
    if centro is None:
        return candidatos[0]
    mejor = min(candidatos, key=lambda c: _km(c[:2], centro))
    return mejor if _km(mejor[:2], centro) <= MAX_KM else None


def _sin_tildes(t: str | None) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


def centros_de_barrio(s: Session) -> dict[str, tuple[float, float]]:
    """Barrio (sin tildes) -> mediana de las coordenadas publicadas por los portales en ese barrio (dentro de CABA).
    Mediana y no promedio: un aviso mal ubicado por el portal (los hay, p. ej. en Córdoba) no corre el centro."""
    acum: dict[str, list[tuple[float, float]]] = {}
    for barrio, lat, lng in s.execute(text("SELECT barrio, lat, lng FROM publicaciones WHERE lat IS NOT NULL "
                                           "AND COALESCE(geo_fuente, 'portal') IN ('portal', 'duplicado')")):
        if en_caba(lat, lng):
            acum.setdefault(_sin_tildes(barrio), []).append((lat, lng))
    return {b: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v)) for b, v in acum.items() if len(v) >= 3}


def copiar_de_duplicados(s: Session) -> int:
    """Opción 1: coordenadas publicadas por otro portal para el mismo inmueble."""
    n = s.execute(text("""
        UPDATE publicaciones SET
          lat = (SELECT o.lat FROM publicaciones o WHERE o.grupo_id = publicaciones.grupo_id AND o.id != publicaciones.id
                 AND o.lat IS NOT NULL AND COALESCE(o.geo_fuente, 'portal') = 'portal' ORDER BY o.id LIMIT 1),
          lng = (SELECT o.lng FROM publicaciones o WHERE o.grupo_id = publicaciones.grupo_id AND o.id != publicaciones.id
                 AND o.lat IS NOT NULL AND COALESCE(o.geo_fuente, 'portal') = 'portal' ORDER BY o.id LIMIT 1),
          geo_fuente = 'duplicado'
        WHERE lat IS NULL AND grupo_id IS NOT NULL AND EXISTS (
          SELECT 1 FROM publicaciones o WHERE o.grupo_id = publicaciones.grupo_id AND o.id != publicaciones.id
          AND o.lat IS NOT NULL AND COALESCE(o.geo_fuente, 'portal') = 'portal')""")).rowcount
    s.commit()
    return n


def geocodificar(s: Session, limite: int = 50, consultar=usig, dormir=time.sleep) -> dict:
    """Opción 2: hasta `limite` avisos activos sin coordenadas, por su dirección. Devuelve un resumen."""
    out = {"duplicado": copiar_de_duplicados(s), "direccion": 0, "sin_resultado": 0, "consultas": 0, "errores": 0}
    centros = centros_de_barrio(s)
    pendientes = s.scalars(select(Publicacion).where(
        Publicacion.lat.is_(None), Publicacion.activa.is_(True), Publicacion.direccion.is_not(None))
        .order_by(Publicacion.id.desc()).limit(limite * 4)).all()
    for p in pendientes:
        q = normalizar(p.direccion)
        if not q:
            continue
        clave = f"{q} | {_sin_tildes(p.barrio)}"[:200]      # el barrio decide entre candidatos: es parte de la clave
        g = s.get(Geocache, clave)
        if g is not None and g.lat is None and datetime.now() - g.fecha > REINTENTO:
            s.delete(g)                                       # no encontrada hace mucho: se vuelve a probar
            s.flush()
            g = None
        if g is None:
            if out["consultas"] >= limite:
                break
            if out["consultas"]:
                dormir(PAUSA)
            out["consultas"] += 1
            try:
                r = elegir(consultar(q), centros.get(_sin_tildes(p.barrio)))
            except Exception as e:  # noqa: BLE001 - red caída o servicio con problemas: se reintenta en otra tanda
                log.warning("geocodificación de %r: %s", q, e)
                out["errores"] += 1
                continue
            g = Geocache(direccion=clave, lat=r[0] if r else None, lng=r[1] if r else None,
                         resultado=r[2] if r else None, fecha=datetime.now())
            s.add(g)
        if g.lat is None:
            out["sin_resultado"] += 1
            continue
        p.lat, p.lng, p.geo_fuente = g.lat, g.lng, "direccion"
        out["direccion"] += 1
    s.commit()
    if any(out[k] for k in ("duplicado", "direccion", "errores")):
        log.info("geolocalización: %s", out)
    return out


def run_forever(engine, stop: threading.Event, intervalo: float = 600) -> None:
    """Una tanda cada `intervalo` segundos (la primera, un minuto después de arrancar)."""
    stop.wait(60)
    while not stop.is_set():
        try:
            with Session(engine) as s:
                geocodificar(s)
        except Exception:  # noqa: BLE001 - nunca debe tumbar la web
            log.exception("error en la geolocalización")
        stop.wait(intervalo)
