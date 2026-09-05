#!/usr/bin/env bash
# Runs ON the EC2 box. Idempotent — safe to re-run on every deploy.
#
# Serves the dashboard behind a password with Caddy. Caddy is used rather than
# nginx because it obtains and renews HTTPS certificates on its own when a real
# domain is pointed at the box, and falls back to plain HTTP when there isn't
# one. That fallback is a downgrade, not a plan: basic auth over HTTP sends the
# password in the clear, so DOMAIN should be set as soon as one exists.
set -euo pipefail

APP_DIR=/opt/powerratings
SITE_DIR=/var/www/powerratings
DOMAIN="${DOMAIN:-}"
AUTH_USER="${AUTH_USER:-joe}"
AUTH_HASH="${AUTH_HASH:?AUTH_HASH must be provided (bcrypt, from: caddy hash-password)}"

echo "==> installing packages"
if command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-pip rsync >/dev/null
  PKG=dnf
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-pip python3-venv rsync debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
  PKG=apt
else
  echo "unsupported distro" >&2; exit 1
fi

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> installing caddy"
  if [ "$PKG" = dnf ]; then
    sudo dnf install -y 'dnf-command(copr)' >/dev/null
    sudo dnf copr enable -y @caddy/caddy >/dev/null
    sudo dnf install -y caddy >/dev/null
  else
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
  fi
fi

echo "==> python environment"
sudo mkdir -p "$APP_DIR" "$SITE_DIR"
sudo chown -R "$USER" "$APP_DIR" "$SITE_DIR"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q numpy pandas

echo "==> building once to verify"
cd "$APP_DIR"
"$APP_DIR/.venv/bin/python" build.py
cp "$APP_DIR/index.html" "$SITE_DIR/index.html"

echo "==> caddy config"
if [ -n "$DOMAIN" ]; then
  SITE_ADDR="$DOMAIN"          # triggers automatic HTTPS
else
  SITE_ADDR=":80"              # no cert possible without a real name
fi
sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$SITE_ADDR {
    root * $SITE_DIR
    basic_auth {
        $AUTH_USER $AUTH_HASH
    }
    file_server
    encode gzip
    header {
        # the page is private; keep it out of caches and crawlers
        Cache-Control "no-store"
        X-Robots-Tag "noindex, nofollow"
    }
}
CADDY
sudo systemctl enable --now caddy >/dev/null 2>&1 || true
sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy

echo "==> weekly rebuild (Mondays 11:00 UTC, matching the old GitHub schedule)"
sudo tee /etc/systemd/system/powerratings.service >/dev/null <<UNIT
[Unit]
Description=Rebuild sports power ratings
After=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/build.py
ExecStartPost=/bin/cp $APP_DIR/index.html $SITE_DIR/index.html
UNIT
sudo tee /etc/systemd/system/powerratings.timer >/dev/null <<UNIT
[Unit]
Description=Weekly rebuild of sports power ratings

[Timer]
OnCalendar=Mon *-*-* 11:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now powerratings.timer >/dev/null

echo "==> done"
systemctl is-active caddy powerratings.timer || true
