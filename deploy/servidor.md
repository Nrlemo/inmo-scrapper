# Producción en un servidor (Docker + túnel de Cloudflare)

Inmo corre en un servidor con Docker; la extensión del navegador queda en la compu de quien la usa y le manda las páginas
por HTTPS (`https://inmo.tudominio.com/api/navegador/*`). El servidor publica la web con un túnel de Cloudflare que ya
administra (`cloudflared` con un `config.yaml` local); no hace falta abrir puertos.

```
 desktop                                    servidor
 ┌─────────────────────┐   https   ┌──────────────────────────────────────────────┐
 │ Vivaldi + extensión │ ────────▶ │ cloudflared ─▶ 127.0.0.1:8095 ─▶ inmo-web    │
 └─────────────────────┘ (túnel)   │                                  ./data ./config │
                                   └──────────────────────────────────────────────┘
```

Archivos: [`deploy/servidor/docker-compose.yml`](servidor/docker-compose.yml) y
[`deploy/servidor/.env.example`](servidor/.env.example).

## Instalación desde cero
En el servidor (usuario en el grupo `docker`, UID 1000):
```bash
mkdir -p ~/.docker/inmo/data ~/.docker/inmo/config && cd ~/.docker/inmo
# copiar docker-compose.yml y .env.example (como .env) de deploy/servidor/, y ajustar .env
docker compose up -d
curl -s http://127.0.0.1:8095/healthz        # ok
```
En el `config.yaml` del túnel, antes de la regla final `- service: http_status:404`:
```yaml
  - hostname: inmo.tudominio.com
    service: http://127.0.0.1:8095
```
Reiniciar el túnel (`sudo systemctl restart cloudflared`) y apuntar el DNS al túnel del servidor:
`cloudflared tunnel route dns --overwrite-dns <ID-del-túnel> inmo.tudominio.com`. Después, el asistente de instalación
(`/setup`) o, si se migra, los pasos que siguen.

## Migrar desde otra máquina (sin perder datos)
Corte de unos minutos. Con `ssh server1` configurado en `~/.ssh/config`.

1. **Que no haya una ronda en curso** (Estado → Ronda). Si la hay, esperar a que termine o cancelarla.
2. **Parar la web vieja** para congelar escrituras (servicio local: `systemctl --user stop inmo-web`).
3. **Copia consistente de la base** con la API de backup de SQLite (nunca `cp` de un archivo en uso: puede quedar a
   medias el WAL):
   ```bash
   python3 -c "import sqlite3; s=sqlite3.connect('file:inmo.sqlite?mode=ro', uri=True); d=sqlite3.connect('inmo-migracion.sqlite'); s.backup(d); d.close()"
   python3 -c "import sqlite3; print(sqlite3.connect('inmo-migracion.sqlite').execute('PRAGMA integrity_check').fetchone())"
   ```
4. **Subir la base y `profiles.yaml`:** `scp inmo-migracion.sqlite server1:~/.docker/inmo/data/inmo.sqlite` y
   `scp profiles.yaml server1:~/.docker/inmo/config/`.
5. **Levantar en el servidor** (`docker compose up -d`) y comparar conteos (publicaciones, usuarios, tokens) con la base
   original. `curl http://127.0.0.1:8095/healthz` → `ok`.
6. **Mover el dominio:** agregar la regla de ingress, reiniciar el túnel del servidor, `cloudflared tunnel route dns
   --overwrite-dns <túnel-del-servidor> inmo.tudominio.com` y apagar el túnel de la máquina vieja
   (`sudo systemctl disable --now cloudflared`).
7. **Verificar:** `https://inmo.tudominio.com/healthz`, login, Estado. En la extensión, **Probar conexión**: el token viaja
   con la base, no hay que generar otro. Si la extensión apuntaba a `localhost`, cambiar el servidor por el dominio.
8. **Una sola vez:** con el túnel delante se detecta HTTPS (`TRUSTED_PROXY_HOPS=1`), la cookie de sesión pasa a llamarse
   `__Host-…` y todos (navegadores y app Android) inician sesión de nuevo.

## Actualizar
```bash
cd ~/.docker/inmo
sed -i 's/^INMO_TAG=.*/INMO_TAG=1.0.3/' .env      # la versión nueva (ver Releases)
docker compose pull                               # baja la imagen sin cortar el servicio
docker compose stop && cp data/inmo.sqlite "data/inmo.antes-$(date +%F).sqlite"   # copia con la web parada
docker compose up -d
```
Las migraciones de la base corren solas al arrancar.

## Vuelta atrás a la máquina anterior
1. En el servidor: `docker compose stop`.
2. Traer la base del servidor (misma API de backup) si hubo cambios desde la migración.
3. En la máquina anterior: levantar la web (`systemctl --user start inmo-web`) y su túnel
   (`sudo systemctl enable --now cloudflared`).
4. Devolverle el dominio: `cloudflared tunnel route dns --overwrite-dns <túnel-de-esa-máquina> inmo.tudominio.com`.

## Qué respaldar
`~/.docker/inmo/data/inmo.sqlite` (avisos, categorización, usuarios, sesiones, filtros) y `~/.docker/inmo/config/profiles.yaml`. Los
backups automáticos están pendientes en [#15](https://github.com/Nrlemo/inmo-scrapper/issues/15).
