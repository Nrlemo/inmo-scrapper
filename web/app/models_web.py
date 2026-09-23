"""Tablas propias de la web (la SQLite las crea; el scrapper no las toca)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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


class Programacion(WebBase):
    """Corrida automática diaria (una sola fila, id=1). Fechas locales sin zona, como el resto."""
    __tablename__ = "web_programacion"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    activa: Mapped[bool] = mapped_column(Boolean, default=False)
    hora: Mapped[str] = mapped_column(String(5), default="03:00")            # HH:MM
    proxima: Mapped[datetime | None] = mapped_column(DateTime)
    ultima_auto: Mapped[datetime | None] = mapped_column(DateTime)
    modificado_por: Mapped[str | None] = mapped_column(String(128))


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
