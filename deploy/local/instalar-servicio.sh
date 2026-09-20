#!/usr/bin/env bash
# Instala la web como servicio de USUARIO de systemd en esta PC: sin sudo y sin Docker.
# Usa los datos que ya tenés (scrapper/data/inmo.sqlite y scrapper/config/profiles.yaml) y el venv de web/.
#
#   deploy/local/instalar-servicio.sh [--usuario NOMBRE]   instala/actualiza y arranca (default: tu usuario del sistema)
#   deploy/local/instalar-servicio.sh --estado             estado y últimas líneas del log
#   deploy/local/instalar-servicio.sh --desinstalar        quita el servicio (conserva tus datos y web.env)
#
# Configuración: ~/.config/inmo/web.env  (se crea una sola vez; editalo y reiniciá:  systemctl --user restart inmo-web)
# Log:           journalctl --user -u inmo-web -f
# Para que arranque con el equipo aun sin iniciar sesión:  loginctl enable-linger "$USER"
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT="inmo-web.service"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_DIR="$HOME/.config/inmo"
ENV_FILE="$ENV_DIR/web.env"
USUARIO="${USER:-anonimo}"

while [ $# -gt 0 ]; do
  case "$1" in
    --usuario) USUARIO="${2:?falta el nombre}"; shift 2 ;;
    --desinstalar)
      systemctl --user disable --now "$UNIT" 2>/dev/null || true
      rm -f "$UNIT_DIR/$UNIT"
      systemctl --user daemon-reload
      echo "Servicio quitado. Se conservan $ENV_FILE y tus datos en $REPO/scrapper/data."
      exit 0 ;;
    --estado)
      systemctl --user --no-pager status "$UNIT" || true
      echo; journalctl --user -u "$UNIT" -n 15 --no-pager 2>/dev/null || true
      exit 0 ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 2 ;;
  esac
done

command -v systemctl >/dev/null || { echo "Hace falta systemd." >&2; exit 1; }
systemctl --user show-environment >/dev/null 2>&1 || { echo "systemd de usuario no está disponible en esta sesión." >&2; exit 1; }

# 1) venv de la web (se crea si falta)
if [ ! -x "$REPO/web/.venv/bin/uvicorn" ]; then
  echo "Creando el entorno virtual de la web…"
  python3 -m venv "$REPO/web/.venv"
  "$REPO/web/.venv/bin/pip" install -q -r "$REPO/web/requirements.txt"
fi

# 2) configuración (una sola vez; nunca se pisa)
mkdir -p "$ENV_DIR" "$UNIT_DIR"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<E
# Configuración del servicio local inmo-web. Después de editar:  systemctl --user restart inmo-web
INMO_HOST=127.0.0.1
INMO_PORT=8000
INMO_DB=$REPO/scrapper/data/inmo.sqlite
INMO_CONFIG=$REPO/scrapper/config/profiles.yaml

# Autenticación: none (sin login; sólo escucha en 127.0.0.1) | basic (usuarios y contraseñas propios) | authentik
AUTH_MODE=none
# En modo none, con este nombre quedan registrados tus cambios
AUTH_DEFAULT_USER=$USUARIO
TRUSTED_PROXY_HOPS=0

# Cliente HTTP del scrapper (httpx | curl) y contacto para su User-Agent (dato personal: no se versiona)
INMO_HTTP_CLIENT=httpx
INMO_CONTACT=

SCHEDULER_ENABLED=true
E
  chmod 600 "$ENV_FILE"
  echo "Creado $ENV_FILE"
fi

# 3) unidad de systemd
cat > "$UNIT_DIR/$UNIT" <<E
[Unit]
Description=Inmo web (local)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO/web
Environment=INMO_HOST=127.0.0.1 INMO_PORT=8000
EnvironmentFile=$ENV_FILE
ExecStart=$REPO/web/.venv/bin/uvicorn app.main:app --host \${INMO_HOST} --port \${INMO_PORT} --no-proxy-headers
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes

[Install]
WantedBy=default.target
E

systemctl --user daemon-reload
systemctl --user enable "$UNIT" >/dev/null 2>&1
systemctl --user restart "$UNIT"

# 4) verificación
PORT="$(grep -E '^INMO_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2)"; PORT="${PORT:-8000}"
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "Servicio en marcha:  http://127.0.0.1:$PORT   (estado: $0 --estado)"
    exit 0
  fi
  sleep 0.5
done
echo "El servicio no respondió a tiempo. Log:  journalctl --user -u $UNIT -n 30" >&2
exit 1
