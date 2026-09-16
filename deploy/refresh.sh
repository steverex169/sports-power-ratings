#!/usr/bin/env bash
# Runs LOCALLY. Builds here and ships the finished page to the server.
#
#   HOST=1.2.3.4 ./deploy/refresh.sh
#
# Why this exists: the odds API answers this machine but returns 403 to the
# EC2 box — books block datacenter ranges, and AWS most of all. Everything else
# (ESPN, nflverse) works fine from the server, so the box still rebuilds weekly
# on its own; that build simply has no CFB market lines. Running this from a
# residential connection is what puts them on the site.
set -euo pipefail

KEY="${KEY:-$HOME/Downloads/joesmodel.pem}"
HOST="${HOST:?set HOST to the instance public IP or DNS}"
USER_NAME="${USER_NAME:-ubuntu}"
SITE=/var/www/powerratings/index.html
APP_DIR=/opt/powerratings
HERE="$(cd "$(dirname "$0")/.." && pwd)"

# Credentials from the environment, or the odds-feed project's .env next door.
if [ -z "${PS3838_USERNAME:-}" ] && [ -f "$HOME/PycharmProjects/odds feed/.env" ]; then
  set -a; . "$HOME/PycharmProjects/odds feed/.env"; set +a
fi

# One record, not two. The weekly rebuild on the server appends to its own
# ledger, so a local build has to continue that file rather than start a rival
# copy — otherwise the page shipped from here would report a different history
# than the page the server builds on Monday.
echo "==> pulling the server's ledger"
mkdir -p "$HERE/data"
if scp -q -i "$KEY" -o StrictHostKeyChecking=accept-new \
     "$USER_NAME@$HOST:$APP_DIR/data/ledger.json" "$HERE/data/ledger.json" 2>/dev/null; then
  echo "    got it"
else
  echo "    none on the server yet — starting one"
fi

echo "==> building locally"
"$HERE/.venv/bin/python" "$HERE/build.py"

PRICED=$("$HERE/.venv/bin/python" - <<'PY'
import json, re
h = open("index.html", encoding="utf-8").read()
g = json.loads(re.search(r"const CFB_GAMES=(\[.*?\]);\n", h, re.S).group(1))
print(sum(1 for x in g if x.get("mkt") is not None))
PY
)
echo "==> CFB games carrying a market line: $PRICED"
if [ "$PRICED" = "0" ]; then
  echo "    (none priced — either books have not hung this week's lines yet," >&2
  echo "     or this machine is also being refused by the odds API)" >&2
fi

echo "==> shipping to $HOST"
scp -q -i "$KEY" -o StrictHostKeyChecking=accept-new "$HERE/index.html" "$USER_NAME@$HOST:/tmp/index.html"
ssh -i "$KEY" "$USER_NAME@$HOST" "sudo install -m 644 -o $USER_NAME -g $USER_NAME /tmp/index.html $SITE && rm -f /tmp/index.html && ls -l $SITE"

echo "==> pushing the ledger back"
scp -q -i "$KEY" "$HERE/data/ledger.json" "$USER_NAME@$HOST:/tmp/ledger.json"
ssh -i "$KEY" "$USER_NAME@$HOST" \
  "install -m 644 /tmp/ledger.json $APP_DIR/data/ledger.json && rm -f /tmp/ledger.json"
echo "==> done"
