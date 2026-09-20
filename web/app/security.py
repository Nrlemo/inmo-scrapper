"""Primitivas de seguridad: hash de claves (argon2id), política de claves/usuarios, tokens y límite de intentos."""
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2id con los parámetros por defecto de argon2-cffi (RFC 9106, perfil de baja memoria: t=3, m=64 MiB, p=4)
_ph = PasswordHasher()
_DUMMY = _ph.hash("clave-ficticia-para-igualar-tiempos")


def hash_clave(clave: str) -> str:
    return _ph.hash(clave)


def verificar_clave(hash_: str, clave: str) -> bool:
    try:
        return _ph.verify(hash_, clave)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def necesita_rehash(hash_: str) -> bool:
    return _ph.check_needs_rehash(hash_)


def verificar_falso(clave: str) -> None:
    """Costo equivalente a verificar una clave real: evita revelar por tiempos si el usuario existe."""
    verificar_clave(_DUMMY, clave)


# ---- usuarios y claves ----
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
CLAVE_MIN, CLAVE_MAX = 12, 128
_COMUNES = {"password1234", "contrasena123", "contraseña123", "qwertyuiop12", "administrador", "admin1234567",
            "123456789012", "1234567890ab", "iloveyou1234", "passw0rd1234", "letmein12345", "welcome12345",
            "changeme1234", "inmobiliaria", "argentina1234", "boca-river123", "mercadolibre1"}


def normalizar_usuario(u: str) -> str:
    return (u or "").strip().lower()


def validar_usuario(u: str) -> str | None:
    if not USER_RE.match(u):
        return "Usuario inválido: 3 a 32 caracteres (letras minúsculas, números, punto, guion o guion bajo)."
    return None


def validar_clave(clave: str, usuario: str = "") -> str | None:
    """Política estilo NIST 800-63B: largo mínimo, sin reglas de composición, rechaza claves triviales."""
    if len(clave) < CLAVE_MIN:
        return f"La contraseña debe tener al menos {CLAVE_MIN} caracteres."
    if len(clave) > CLAVE_MAX:
        return f"La contraseña no puede superar {CLAVE_MAX} caracteres."
    low = clave.lower()
    if low in _COMUNES or len(set(low)) < 5:
        return "Esa contraseña es demasiado común o repetitiva."
    if usuario and len(usuario) >= 3 and usuario in low:
        return "La contraseña no puede contener el nombre de usuario."
    return None


def generar_clave(n: int = 16) -> str:
    alfabeto = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin caracteres ambiguos
    while True:
        c = "".join(secrets.choice(alfabeto) for _ in range(n))
        if any(x.isdigit() for x in c) and any(x.islower() for x in c) and any(x.isupper() for x in c):
            return c


# ---- tokens ----
def nuevo_token(n: int = 32) -> str:
    return secrets.token_urlsafe(n)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def iguales(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def next_seguro(destino: str | None) -> str:
    """Sólo rutas relativas del propio sitio (evita open redirect)."""
    d = destino or ""
    if (d.startswith("/") and not d.startswith("//") and "\\" not in d and "\n" not in d and "\r" not in d
            and len(d) < 500 and not d.startswith(("/login", "/setup", "/logout"))):
        return d
    return "/"


# ---- límite de intentos (memoria del proceso) ----
class Limitador:
    def __init__(self, maximo: int, ventana_s: float):
        self.maximo, self.ventana = maximo, ventana_s
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> bool:
        """Registra el intento; False si ya se superó el máximo dentro de la ventana."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[clave]
            while q and now - q[0] > self.ventana:
                q.popleft()
            if len(q) >= self.maximo:
                return False
            q.append(now)
            if len(self._hits) > 5000:  # limpieza básica
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.ventana]:
                    self._hits.pop(k, None)
            return True
