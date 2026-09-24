"""Estado → Backups: último backup, lista, «Hacer backup ahora» y descarga (sólo administradores)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from . import backups, config
from .core import ctx, puede_administrar, render

router = APIRouter()


def panel_ctx(user) -> dict:
    return {"bk": backups.estado(), "bk_lista": backups.listar()[:20], "bk_puede": puede_administrar(user),
            "bk_hora": config.BACKUP_HORA, "bk_activo": config.BACKUP_ENABLED, "bk_extra": config.BACKUP_DIR_EXTRA,
            "bk_retencion": (config.BACKUP_DIARIOS, config.BACKUP_SEMANALES, config.BACKUP_MENSUALES)}


def _admin(c):
    if not puede_administrar(c["user"]):
        raise HTTPException(403, "Sólo un administrador puede manejar los backups")


@router.post("/backups/ahora", response_class=HTMLResponse)
async def ahora(request: Request, c=Depends(ctx)):
    _admin(c)
    await run_in_threadpool(backups.hacer_backup, None, f"manual ({c['user'].username})")
    return render(request, "partials/backups.html", c, **panel_ctx(c["user"]))


@router.get("/backups/{nombre}")
def descargar(nombre: str, c=Depends(ctx)):
    """Incluye hashes de contraseñas y sesiones: sólo administradores."""
    _admin(c)
    if not backups.NOMBRE_RE.match(nombre) or not (backups._dir() / nombre).is_file():
        raise HTTPException(404)
    return FileResponse(backups._dir() / nombre, media_type="application/gzip", filename=nombre)
