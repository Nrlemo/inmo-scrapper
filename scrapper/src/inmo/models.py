"""Esquema SQLite (SQLAlchemy 2.0)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ESTADOS = ("nuevo", "interesante", "para visitar", "descartado", "contactado")


class Base(DeclarativeBase):
    pass


class Inmobiliaria(Base):
    __tablename__ = "inmobiliarias"
    __table_args__ = (UniqueConstraint("portal", "id_externo", name="uq_inmo_portal_id_externo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portal: Mapped[str] = mapped_column(String(32))
    id_externo: Mapped[str] = mapped_column(String(64))  # derivado de la ruta del logo (estable)
    nombre: Mapped[str | None] = mapped_column(String(200))  # aproximado (slug del logo)
    logo_url: Mapped[str | None] = mapped_column(Text)
    fecha_primera_vista: Mapped[datetime] = mapped_column(DateTime)


class Publicacion(Base):
    __tablename__ = "publicaciones"
    __table_args__ = (UniqueConstraint("portal", "id_externo", name="uq_portal_id_externo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portal: Mapped[str] = mapped_column(String(32), index=True)
    id_externo: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    titulo: Mapped[str | None] = mapped_column(Text)
    direccion: Mapped[str | None] = mapped_column(Text)
    barrio: Mapped[str | None] = mapped_column(String(128), index=True)
    precio: Mapped[float | None] = mapped_column(Float)
    moneda: Mapped[str | None] = mapped_column(String(3))
    expensas: Mapped[float | None] = mapped_column(Float)
    moneda_expensas: Mapped[str | None] = mapped_column(String(3))
    ambientes: Mapped[int | None] = mapped_column(Integer)
    dormitorios: Mapped[int | None] = mapped_column(Integer)
    banos: Mapped[int | None] = mapped_column(Integer)
    cocheras: Mapped[int | None] = mapped_column(Integer)
    m2_cubiertos: Mapped[float | None] = mapped_column(Float)
    m2_totales: Mapped[float | None] = mapped_column(Float)
    descripcion: Mapped[str | None] = mapped_column(Text)
    fotos: Mapped[list[str]] = mapped_column(JSON, default=list)
    fecha_primera_vista: Mapped[datetime] = mapped_column(DateTime)
    fecha_ultima_vista: Mapped[datetime] = mapped_column(DateTime, index=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    consultas_sin_ver: Mapped[int] = mapped_column(Integer, default=0)
    # Misma propiedad en distintos portales comparten grupo_id (id de la publicación "raíz").
    grupo_id: Mapped[int | None] = mapped_column(Integer, index=True)
    inmobiliaria_id: Mapped[int | None] = mapped_column(ForeignKey("inmobiliarias.id"), index=True)
    corredor: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    historial: Mapped[list[HistorialPrecio]] = relationship(
        back_populates="publicacion", order_by="HistorialPrecio.fecha", cascade="all, delete-orphan"
    )
    categorizacion: Mapped[Categorizacion | None] = relationship(
        back_populates="publicacion", uselist=False, cascade="all, delete-orphan"
    )


class Consulta(Base):
    __tablename__ = "consultas"

    id: Mapped[int] = mapped_column(primary_key=True)
    perfil: Mapped[str] = mapped_column(String(64), index=True)
    portal: Mapped[str] = mapped_column(String(32), index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, index=True)
    cantidad_resultados: Mapped[int] = mapped_column(Integer, default=0)
    completa: Mapped[bool] = mapped_column(Boolean, default=False)
    bloqueada: Mapped[bool] = mapped_column(Boolean, default=False)
    errores: Mapped[str | None] = mapped_column(Text)


class HistorialPrecio(Base):
    __tablename__ = "historial_precios"

    id: Mapped[int] = mapped_column(primary_key=True)
    publicacion_id: Mapped[int] = mapped_column(ForeignKey("publicaciones.id"), index=True)
    precio: Mapped[float] = mapped_column(Float)
    moneda: Mapped[str] = mapped_column(String(3))
    fecha: Mapped[datetime] = mapped_column(DateTime)
    # % respecto al precio anterior (negativo = baja, positivo = suba). NULL en el primer registro
    # o si la moneda cambió (no comparable).
    variacion_pct: Mapped[float | None] = mapped_column(Float)

    publicacion: Mapped[Publicacion] = relationship(back_populates="historial")


class Categorizacion(Base):
    __tablename__ = "categorizacion"

    publicacion_id: Mapped[int] = mapped_column(ForeignKey("publicaciones.id"), primary_key=True)
    estado: Mapped[str] = mapped_column(String(16), default="nuevo", index=True)
    puntaje: Mapped[int | None] = mapped_column(Integer)  # 1-5
    notas: Mapped[str | None] = mapped_column(Text)
    etiquetas: Mapped[list[str]] = mapped_column(JSON, default=list)
    etiquetas_auto: Mapped[list[str] | None] = mapped_column(JSON, default=list)  # por palabras clave (tags.py)
    fecha_modificacion: Mapped[datetime] = mapped_column(DateTime)

    publicacion: Mapped[Publicacion] = relationship(back_populates="categorizacion")


class Ejecucion(Base):
    """Corrida del scrapper con su progreso (la lanza la web; el CLI la actualiza con --run-id)."""
    __tablename__ = "ejecuciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    portal: Mapped[str] = mapped_column(String(32))
    zonas: Mapped[list[str]] = mapped_column(JSON, default=list)   # vacío = todas
    usuario: Mapped[str | None] = mapped_column(String(128))
    inicio: Mapped[datetime] = mapped_column(DateTime)
    fin: Mapped[datetime | None] = mapped_column(DateTime)
    actualizado: Mapped[datetime] = mapped_column(DateTime)
    # corriendo | ok | parcial | bloqueada | omitida | error | cancelada | interrumpida
    estado: Mapped[str] = mapped_column(String(16), index=True)
    zonas_total: Mapped[int] = mapped_column(Integer, default=0)
    zona_idx: Mapped[int] = mapped_column(Integer, default=0)      # 1-based
    zona_actual: Mapped[str | None] = mapped_column(String(80))
    pagina: Mapped[int] = mapped_column(Integer, default=0)        # páginas ya leídas de la zona actual
    paginas: Mapped[int] = mapped_column(Integer, default=0)
    resultados: Mapped[int] = mapped_column(Integer, default=0)
    mensaje: Mapped[str | None] = mapped_column(Text)
    pid: Mapped[int | None] = mapped_column(Integer)


def make_engine(path: str) -> Engine:
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    Base.metadata.create_all(engine)
    _migrate(engine)
    return engine


def _migrate(engine: Engine) -> None:
    """create_all no altera tablas existentes: agrega columnas nuevas a bases creadas antes."""
    with engine.begin() as c:
        pub = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(publicaciones)")}
        if pub and "inmobiliaria_id" not in pub:
            c.exec_driver_sql("ALTER TABLE publicaciones ADD COLUMN inmobiliaria_id INTEGER REFERENCES inmobiliarias(id)")
            c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_publicaciones_inmobiliaria_id ON publicaciones(inmobiliaria_id)")
        if pub and "corredor" not in pub:
            c.exec_driver_sql("ALTER TABLE publicaciones ADD COLUMN corredor TEXT")
        for col in ("lat", "lng"):
            if pub and col not in pub:
                c.exec_driver_sql(f"ALTER TABLE publicaciones ADD COLUMN {col} FLOAT")
        cat = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(categorizacion)")}
        if cat and "etiquetas_auto" not in cat:
            c.exec_driver_sql("ALTER TABLE categorizacion ADD COLUMN etiquetas_auto JSON")
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(historial_precios)")}
        if "variacion_pct" not in cols:
            c.exec_driver_sql("ALTER TABLE historial_precios ADD COLUMN variacion_pct FLOAT")
            # Backfill: variación de cada registro contra el anterior de la misma publicación y moneda.
            c.exec_driver_sql("""
                UPDATE historial_precios SET variacion_pct = (
                    SELECT ROUND((historial_precios.precio - h.precio) * 100.0 / h.precio, 2)
                    FROM historial_precios h
                    WHERE h.publicacion_id = historial_precios.publicacion_id
                      AND h.moneda = historial_precios.moneda AND h.precio > 0
                      AND (h.fecha < historial_precios.fecha
                           OR (h.fecha = historial_precios.fecha AND h.id < historial_precios.id))
                    ORDER BY h.fecha DESC, h.id DESC LIMIT 1)""")
