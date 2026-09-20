class BlockedError(Exception):
    """El portal bloqueó o desafió el acceso (403/429/challenge). No se debe insistir."""
