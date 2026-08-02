#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="systemd"
UPGRADE=0

usage() {
  cat <<'EOF'
TAO Linux installer

Usage:
  ./deploy/install.sh --docker
  sudo ./deploy/install.sh --systemd
  sudo ./deploy/install.sh --upgrade

Modes:
  --docker   Configure and start Docker Compose in the current checkout.
  --systemd  Install TAO to /opt/tao with config in /etc/tao and data in /var/lib/tao.
  --upgrade  Refresh an existing systemd installation while preserving config and data.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --docker) MODE="docker" ;;
    --systemd) MODE="systemd" ;;
    --upgrade) MODE="systemd"; UPGRADE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

install_docker() {
  command -v docker >/dev/null 2>&1 || { echo "Docker is required." >&2; exit 1; }
  docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required." >&2; exit 1; }
  cd "$SOURCE_DIR"
  mkdir -p config data
  if [[ ! -f config/tao.env ]]; then
    docker compose run --rm --no-deps tao \
      tao setup --headless --non-interactive \
      --env-file /config/tao.env --deploy server --frontend web --data-dir /data
  fi
  docker compose pull
  docker compose up -d
  echo "TAO Docker deployment is running."
  echo "Check: docker compose ps"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "systemd installation requires root: sudo $0 --systemd" >&2
    exit 1
  fi
}

install_python_tools() {
  if ! command -v python3 >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 python3-venv python3-pip
  elif ! python3 -m venv --help >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3-venv
  fi
}

install_systemd() {
  require_root
  install_python_tools
  id tao >/dev/null 2>&1 || useradd --system --home /var/lib/tao --shell /usr/sbin/nologin tao
  install -d -o tao -g tao -m 0750 /var/lib/tao
  install -d -o root -g tao -m 0750 /etc/tao

  if [[ "$UPGRADE" -eq 1 && -f /etc/tao/tao.env ]]; then
    cp -a /etc/tao/tao.env "/etc/tao/tao.env.bak"
  fi

  rm -rf /opt/tao.new
  install -d -m 0755 /opt/tao.new
  cp -a "$SOURCE_DIR"/. /opt/tao.new/
  rm -rf /opt/tao.new/.git /opt/tao.new/.venv /opt/tao.new/data
  python3 -m venv /opt/tao.new/.venv
  /opt/tao.new/.venv/bin/python -m pip install --upgrade pip
  /opt/tao.new/.venv/bin/python -m pip install "/opt/tao.new[bot]"

  if [[ ! -f /etc/tao/tao.env ]]; then
    /opt/tao.new/.venv/bin/tao setup --headless \
      --headless --env-file /etc/tao/tao.env --deploy server --frontend web \
      --data-dir /var/lib/tao
  fi

  systemctl stop tao.service 2>/dev/null || true
  rm -rf /opt/tao.old
  if [[ -d /opt/tao ]]; then mv /opt/tao /opt/tao.old; fi
  mv /opt/tao.new /opt/tao
  chown -R root:root /opt/tao
  chown root:tao /etc/tao/tao.env
  chmod 0640 /etc/tao/tao.env
  install -m 0644 "$SOURCE_DIR/deploy/tao.service" /etc/systemd/system/tao.service
  systemctl daemon-reload
  systemctl enable --now tao.service
  echo "TAO systemd deployment is running."
  echo "Status: systemctl status tao"
  echo "Logs:   journalctl -u tao -f"
}

if [[ "$MODE" == "docker" ]]; then
  install_docker
else
  install_systemd
fi
