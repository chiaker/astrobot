#!/usr/bin/env bash
# One-time VPS provisioning for astrobot.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<user>/astrobot/main/scripts/bootstrap-server.sh | sudo bash -s -- <repo-url>
# Or after manual copy:
#   sudo ./bootstrap-server.sh https://github.com/<user>/astrobot.git
set -euo pipefail

REPO_URL="${1:-}"
if [[ -z "$REPO_URL" ]]; then
  echo "Usage: $0 <repo-url>" >&2
  exit 1
fi

APP_DIR="/opt/astrobot"

echo "==> Updating apt"
apt-get update -y

echo "==> Installing prerequisites"
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg git ufw

echo "==> Installing Docker (official repo)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

echo "==> Cloning repo to ${APP_DIR}"
if [[ ! -d "$APP_DIR/.git" ]]; then
  mkdir -p "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ""
  echo "==> Created .env from .env.example."
  echo "    Edit /opt/astrobot/.env and set:"
  echo "      BOT_TOKEN, LLM_API_KEY, LLM_BASE_URL,"
  echo "      RUN_MODE=webhook, BOT_DOMAIN, WEBHOOK_BASE_URL, WEBHOOK_SECRET,"
  echo "      ADMIN_PASSWORD, ADMIN_SECRET, GRAFANA_PASSWORD"
  echo "    Then re-run: cd /opt/astrobot && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
fi

echo "==> Configuring firewall (ufw)"
ufw allow OpenSSH || true
ufw allow 80/tcp || true
ufw allow 443/tcp || true
yes | ufw enable || true

mkdir -p "$APP_DIR/backups"

echo ""
echo "==> Bootstrap done."
echo "Next steps:"
echo "  1. Edit /opt/astrobot/.env"
echo "  2. (If GHCR package is private) docker login ghcr.io -u <github-user>"
echo "  3. docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
