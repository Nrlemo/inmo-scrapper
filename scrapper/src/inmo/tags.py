"""Etiquetas automáticas: palabras clave del título/descripción -> etiquetas, según `auto_tags` del YAML.

    auto_tags:
      patio: ["patio"]
      apto_credito: ["apto crédito", "apto credito", "apto banco"]

Sin distinguir mayúsculas ni tildes y por palabra completa. Una mención negada («sin cochera», «no tiene
balcón», «ni patio») no cuenta. Se guardan en categorizacion.etiquetas_auto, separadas de las que carga el
usuario a mano (categorizacion.etiquetas), así nunca se pisan.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Categorizacion, Publicacion

# Palabras que niegan la mención que les sigue inmediatamente (con o sin un verbo en el medio).
_NEGACION = r"(?<!\bsin )(?<!\bni )(?<!\bno )(?<!\bno tiene )(?<!\bno posee )(?<!\bno cuenta con )"


def normalizar(t: str | None) -> str:
    """minúsculas, sin tildes, todo lo que no es letra o número pasa a un espacio."""
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def compilar(reglas: dict | None) -> list[tuple[str, re.Pattern]]:
    """Valida y compila las reglas del YAML. ValueError con un mensaje claro si el formato no es el esperado."""
    if not reglas:
        return []
    if not isinstance(reglas, dict):
        raise ValueError("auto_tags tiene que ser un diccionario: etiqueta: [palabras clave]")
    out = []
    for etiqueta, frases in reglas.items():
        frases = [frases] if isinstance(frases, str) else frases
        if not isinstance(frases, list) or not all(isinstance(f, str) for f in frases):
            raise ValueError(f"auto_tags.{etiqueta}: tiene que ser una lista de palabras clave")
        alternativas = [r"\s".join(map(re.escape, normalizar(f).split())) for f in frases if normalizar(f)]
        if alternativas:
            patron = re.compile(rf"{_NEGACION}\b(?:{'|'.join(alternativas)})\b")
            out.append((str(etiqueta).strip().lower()[:30], patron))
    return out


def etiquetar(texto: str, reglas: list[tuple[str, re.Pattern]]) -> list[str]:
    t = normalizar(texto)
    return sorted({etiqueta for etiqueta, patron in reglas if patron.search(t)})


def aplicar(s: Session, reglas_yaml: dict | None) -> int:
    """Recalcula las etiquetas automáticas de todas las publicaciones. Devuelve cuántas cambiaron.

    Se corre entero (no sólo sobre lo nuevo) para que un cambio en las reglas se refleje en todo lo guardado.
    """
    reglas = compilar(reglas_yaml)
    n = 0
    filas = s.execute(select(Categorizacion, Publicacion.titulo, Publicacion.descripcion)
                      .join(Publicacion, Publicacion.id == Categorizacion.publicacion_id))
    for cat, titulo, descripcion in filas:
        nuevas = etiquetar(f"{titulo or ''} {descripcion or ''}", reglas)
        if (cat.etiquetas_auto or []) != nuevas:
            cat.etiquetas_auto = nuevas
            n += 1
    return n
