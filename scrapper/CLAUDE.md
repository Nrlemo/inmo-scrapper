# scrapper/ (paquete `inmo`)

Núcleo que comparte la web: modelos de la SQLite, parsers de los portales y la lógica de la ronda por navegador.
El nombre de la carpeta es histórico: **ya no hay scrapper HTTP**. El servidor no pide páginas a los portales; la
extensión ([Nrlemo/inmo-extension](https://github.com/Nrlemo/inmo-extension)) las abre en el navegador del usuario
y las manda a la web (`/api/navegador/*`). Las reglas generales del proyecto están en el `CLAUDE.md` de la raíz.

## Módulos (`src/inmo/`)
- `navegador.py`: conduce la ronda página a página (qué sigue, pausas, tope de `robots.txt`, bloqueo y cooldown,
  bajas sólo tras una ronda completa y no truncada). El estado vive en la base (`RondaNavegador` + `Ejecucion`).
- `connectors/`: un módulo por portal con sus parsers (`zonaprop.py`); `common.py` tiene el parseo de montos, los
  filtros del perfil (`matches_profile`) y la detección del desafío anti-bot (`es_desafio`); `base.py` define `Listing`.
- `repo.py`: upsert con historial de precios, bajas (`mark_missing`) y vínculo entre portales (`link_duplicates`).
- `models.py`: esquema SQLAlchemy y migraciones livianas (`_migrate`: agrega columnas a bases existentes).
- `filtros.py`: filtros de búsqueda editables desde la web (documento JSON guardado por la web): importación desde
  `profiles.yaml`, validación, conversión a los perfiles de la ronda y `recalcular()` (marca `fuera_filtro`).
- `tags.py`: etiquetas automáticas (`auto_tags` del YAML). `__main__.py`: `python -m inmo retag`.
- `config.py`: carga de `profiles.yaml` (cortesía y `auto_tags`; los perfiles sólo como importación inicial) e
  `INMO_INACTIVE_AFTER`.

## Reglas
- Un parser nuevo se prueba con **HTML real** guardado desde el navegador (`tests/fixtures/`), sin red. Si la
  estructura del portal es incierta, se avisa en lugar de inventar selectores.
- Ante un dato ausente, `matches_profile` no descarta el aviso.
- Nunca pisar con `None` un dato que ya teníamos (ver `repo.upsert`).
- Las fechas son locales, sin zona horaria.

## Tests
```bash
cd scrapper && .venv/bin/python -m pytest -q
```
