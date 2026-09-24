"""Tablas propias de la web (la SQLite las crea; el scrapper no las toca)."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WebBase(DeclarativeBase):
    pass


class Usuario(WebBase):
    __tablename__ = "web_usuarios"
    username: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(256))
    ultima_visita: Mapped[datetime] = mapped_column(DateTime)
    visita_previa: Mapped[datetime | None] = mapped_column(DateTime)  # inicio de la sesión anterior


class Revision(WebBase):
    """Estado compartido de revisión de cada publicación."""
    __tablename__ = "web_revision"
    publicacion_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FK lógica a publicaciones.id
    favorito: Mapped[bool] = mapped_column(Boolean, default=False)
    potencial: Mapped[bool] = mapped_column(Boolean, default=False)
    descartada: Mapped[bool] = mapped_column(Boolean, default=False)
    contactada: Mapped[bool] = mapped_column(Boolean, default=False)
    revisada: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fecha_contacto: Mapped[datetime | None] = mapped_column(DateTime)
    notas: Mapped[str | None] = mapped_column(Text)
    modificado_por: Mapped[str | None] = mapped_column(String(128))
    fecha: Mapped[datetime] = mapped_column(DateTime)


class Puntaje(WebBase):
    """Puntaje 1–5 que cada usuario le da a una publicación (el de `categorizacion` es el promedio redondeado)."""
    __tablename__ = "web_puntajes"
    publicacion_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FK lógica a publicaciones.id
    usuario: Mapped[str] = mapped_column(String(128), primary_key=True)
    puntaje: Mapped[int] = mapped_column(Integer)
    fecha: Mapped[datetime] = mapped_column(DateTime)


class Evento(WebBase):
    __tablename__ = "web_eventos"
    id: Mapped[int] = mapped_column(primary_key=True)
    publicacion_id: Mapped[int] = mapped_column(Integer, index=True)
    usuario: Mapped[str] = mapped_column(String(128))
    tipo: Mapped[str] = mapped_column(String(32))
    detalle: Mapped[str | None] = mapped_column(Text)
    fecha: Mapped[datetime] = mapped_column(DateTime)


class BusquedaGuardada(WebBase):
    __tablename__ = "web_busquedas"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    querystring: Mapped[str] = mapped_column(Text)
    usuario: Mapped[str] = mapped_column(String(128))


class VsBarrio(WebBase):
    """USD/m² de cada aviso frente a la mediana de su barrio (app/mercado.py). Se recalcula cuando cambian los datos."""
    __tablename__ = "web_vs_barrio"
    publicacion_id: Mapped[int] = mapped_column(Integer, primary_key=True)   # FK lógica a publicaciones.id
    pct: Mapped[float] = mapped_column(Float, index=True)       # negativo = más barato que la referencia
    ref: Mapped[str] = mapped_column(String(80))                # «Almagro 3 amb.» o «Almagro»
    n: Mapped[int] = mapped_column(Integer)                     # tamaño de la muestra


class ConfigBusqueda(WebBase):
    """Filtros de búsqueda editables en Estado (una sola fila, id=1). Formato en inmo.filtros; la primera vez se
    importan de profiles.yaml."""
    __tablename__ = "web_config_busqueda"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    datos: Mapped[dict] = mapped_column(JSON)
    modificado_por: Mapped[str | None] = mapped_column(String(128))
    fecha: Mapped[datetime] = mapped_column(DateTime)


class TokenApi(WebBase):
    """Token de la extensión del navegador (ronda por navegador). Uno por usuario; se guarda sólo el hash."""
    __tablename__ = "web_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario: Mapped[str] = mapped_column(String(128), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    creado: Mapped[datetime] = mapped_column(DateTime)
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime)


class Cuenta(WebBase):
    """Cuenta de usuario del modo de autenticación `basic`."""
    __tablename__ = "web_cuentas"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)   # siempre en minúsculas
    password_hash: Mapped[str] = mapped_column(String(255))                      # argon2id
    rol: Mapped[str] = mapped_column(String(10), default="usuario")              # admin | usuario
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    debe_cambiar: Mapped[bool] = mapped_column(Boolean, default=False)           # clave temporal: cambiarla al entrar
    creado: Mapped[datetime] = mapped_column(DateTime)
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime)
    fallos: Mapped[int] = mapped_column(Integer, default=0)                      # intentos fallidos seguidos
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime)


class Sesion(WebBase):
    """Sesión del lado del servidor. Se guarda sólo el hash del token de la cookie."""
    __tablename__ = "web_sesiones"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(Integer, index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    creada: Mapped[datetime] = mapped_column(DateTime)
    actividad: Mapped[datetime] = mapped_column(DateTime)
    expira: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str | None] = mapped_column(String(45))
    agente: Mapped[str | None] = mapped_column(String(200))
    recordar: Mapped[bool] = mapped_column(Boolean, default=False)   # «Mantener sesión iniciada»
