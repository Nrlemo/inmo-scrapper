"""Utilidades compartidas por los conectores (parseo de números/montos, texto, filtros del perfil, desafíos anti-bot)."""
from __future__ import annotations

import html
import re
from typing import Any

from .base import Listing

CHALLENGE_TITLES = ("just a moment", "attention required", "un momento")


def _num(s: str) -> float | None:
    """'215.000' -> 215000 ; '76,35' -> 76.35"""
    s = s.strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _money(text: str | None) -> tuple[float | None, str | None]:
    """'USD 215.000' -> (215000, 'USD'); '$ 300.000 Expensas' -> (300000, 'ARS')."""
    if not text:
        return None, None
    m = re.search(r"(USD|U\$S|\$)\s*([\d.,]+)", text)
    if not m:
        return None, None
    return _num(m.group(2)), ("ARS" if m.group(1) == "$" else "USD")


def _clean(s: str | None) -> str | None:
    if not s:
        return None
    s = html.unescape(html.unescape(s))  # la descripción viene doble-escapada (&amp;amp;quot;)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def matches_profile(l: Listing, profile: dict[str, Any]) -> bool:
    """Filtros que la URL no garantiza. Ante dato ausente (None) NO se descarta."""
    price, rooms = profile.get("price", {}), profile.get("rooms", {})
    if l.precio is not None:
        if l.moneda and profile.get("currency") and l.moneda != profile["currency"]:
            return False
        if price.get("min") is not None and l.precio < price["min"]:
            return False
        if price.get("max") is not None and l.precio > price["max"]:
            return False
    if l.ambientes is not None:
        if rooms.get("min") is not None and l.ambientes < rooms["min"]:
            return False
        if rooms.get("max") is not None and l.ambientes > rooms["max"]:
            return False
    min_dorm = profile.get("bedrooms", {}).get("min")
    if min_dorm is not None and l.dormitorios is not None and l.dormitorios < min_dorm:
        return False
    min_tot = profile.get("total_m2_min")  # el listado da m² totales de casi todos los avisos; cubiertos casi nunca
    if min_tot is not None and l.m2_totales is not None and l.m2_totales < min_tot:
        return False
    min_cub = profile.get("covered_m2_min")
    if min_cub is not None and l.m2_cubiertos is not None and l.m2_cubiertos < min_cub:
        return False
    hay = f"{l.titulo or ''} {l.descripcion or ''}".lower()
    return not any(k.lower() in hay for k in profile.get("exclude_keywords", []))


def es_desafio(html: str) -> bool:
    """¿La página es la verificación anti-bot (Cloudflare) y no el listado? Sólo se mira el <title>: la página real
    de los sitios protegidos también menciona "challenge" en sus scripts."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:20000], re.I | re.S)
    title = m.group(1).strip().lower() if m else ""
    return any(t in title for t in CHALLENGE_TITLES)
