"""Identidad por forward-auth de authentik: el proxy valida la sesión y envía las cabeceras."""
import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import config


@dataclass
class User:
    username: str
    email: str | None


def current_user(request: Request) -> User:
    if config.PROXY_SECRET and not hmac.compare_digest(
            request.headers.get("X-Proxy-Secret", ""), config.PROXY_SECRET):
        raise HTTPException(401, "Acceso sólo a través del proxy")
    name = request.headers.get(config.USER_HEADER) or config.DEV_USER
    if not name:
        raise HTTPException(401, "Sin identidad de authentik")
    return User(name, request.headers.get(config.EMAIL_HEADER))
