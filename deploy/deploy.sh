#!/usr/bin/env bash
# Runs LOCALLY. Ships the project to the EC2 box and provisions it.
#
#   HOST=1.2.3.4 ./deploy/deploy.sh                  # http, password-gated
#   HOST=1.2.3.4 DOMAIN=ratings.example.com ./deploy/deploy.sh   # + real HTTPS
#
# Secrets are sent over ssh stdin and written on the server only. They never
# enter the repo, the rsync payload, or this machine's process list.
set -euo pipefail

KEY="${KEY:-$HOME/Downloads/joesmodel.pem}"
HOST="${HOST:?set HOST to the instance public IP or DNS}"
DOMAIN="${DOMAIN:-}"
AUTH_USER="${AUTH_USER:-joe}"
APP_DIR=/opt/powerratings
HERE="$(cd "$(dirname "$0")/.." && pwd)"

chmod 400 "$KEY" 2>/dev/null || true
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

# AMIs differ; find the account that actually answers.
USER_NAME=""
for u in ec2-user ubuntu admin rocky fedora; do
  if $SSH "$u@$HOST" true 2>/dev/null; then USER_NAME="$u"; break; fi
done
[ -n "$USER_NAME" ] || { echo "could not log in as any standard AMI user — check the security group allows port 22 from your IP, and that HOST is right" >&2; exit 1; }
echo "==> connected as $USER_NAME@$HOST"

# Password for the site. Generated once and stored locally so redeploys keep it.
PWFILE="$HERE/deploy/.site-password"
if [ ! -f "$PWFILE" ]; then
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 > "$PWFILE"
  chmod 600 "$PWFILE"
  echo "==> generated a new site password (saved to deploy/.site-password)"
fi
SITE_PW="$(cat "$PWFILE")"

echo "==> syncing project"
rsync -az --delete -e "$SSH" \
  --exclude '.git' --exclude '.venv' --exclude 'data/cache' --exclude 'deploy/.site-password' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude 'pinnacle_env.txt' \
  --rsync-path="sudo mkdir -p $APP_DIR && sudo chown -R $USER_NAME $APP_DIR && rsync" \
  "$HERE/" "$USER_NAME@$HOST:$APP_DIR/"

# Odds credentials: read from this shell's env, written server-side with 600.
if [ -n "${PS3838_USERNAME:-}" ] && [ -n "${PS3838_PASSWORD:-}" ]; then
  echo "==> installing odds credentials on the server"
  # PS3838_PROXY is what makes the server build work at all: the book returns
  # 403 to AWS, so the call needs a residential exit node.
  { printf 'PS3838_BASE_URL=%s\nPS3838_USERNAME=%s\nPS3838_PASSWORD=%s\n' \
      "${PS3838_BASE_URL:-https://api.probet42.com}" "$PS3838_USERNAME" "$PS3838_PASSWORD"
    [ -n "${PS3838_PROXY:-}" ] && printf 'PS3838_PROXY=%s\n' "$PS3838_PROXY"
  } | $SSH "$USER_NAME@$HOST" "cat > $APP_DIR/pinnacle_env.txt && chmod 600 $APP_DIR/pinnacle_env.txt"
else
  # rsync excludes pinnacle_env.txt, so whatever the server already holds
  # survives a redeploy. Only say "no lines" if there really is nothing there.
  if $SSH "$USER_NAME@$HOST" "test -s $APP_DIR/pinnacle_env.txt"; then
    echo "==> no PS3838_* in env; keeping the credentials already on the server"
  else
    echo "==> no PS3838_* in env and none on the server; build will carry no CFB market lines"
  fi
fi

echo "==> provisioning"
HASH=$($SSH "$USER_NAME@$HOST" "command -v caddy >/dev/null 2>&1 && caddy hash-password --plaintext '$SITE_PW' 2>/dev/null" || true)
$SSH "$USER_NAME@$HOST" \
  "DOMAIN='$DOMAIN' AUTH_USER='$AUTH_USER' AUTH_HASH='${HASH:-PLACEHOLDER}' bash -s" < "$HERE/deploy/provision.sh" \
  || { echo "provision failed" >&2; exit 1; }

# Caddy is only present after the first provision, so the hash may need a second pass.
if [ -z "${HASH:-}" ]; then
  echo "==> caddy now installed; setting the password"
  HASH=$($SSH "$USER_NAME@$HOST" "caddy hash-password --plaintext '$SITE_PW'")
  $SSH "$USER_NAME@$HOST" \
    "DOMAIN='$DOMAIN' AUTH_USER='$AUTH_USER' AUTH_HASH='$HASH' bash -s" < "$HERE/deploy/provision.sh"
fi

URL="http://$HOST"; [ -n "$DOMAIN" ] && URL="https://$DOMAIN"
echo
echo "================ live ================"
echo "  URL      : $URL"
echo "  username : $AUTH_USER"
echo "  password : $SITE_PW"
[ -z "$DOMAIN" ] && echo "  NOTE: no domain set, so this is plain HTTP — the password crosses the network in the clear."
echo "======================================"
