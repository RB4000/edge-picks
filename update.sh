#!/usr/bin/env bash
# Idempotent: refresh data, capture/freeze lines, lock picks, regrade, rebuild ./site.
# Safe to run repeatedly or from cron; a file lock prevents overlapping runs.
#   AUTO_DEPLOY=1        also deploy to Cloudflare Pages after a successful update
#   LEDGER_AUTOCOMMIT=1  git-commit any ledger changes (public, timestamped audit trail)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p data/logs

if [ ! -x .venv/bin/python ]; then
  echo "No virtualenv found. Run: make setup" >&2
  exit 1
fi

.venv/bin/python -m nfledge.pipeline update

if [ "${LEDGER_AUTOCOMMIT:-0}" = "1" ] && [ -d .git ]; then
  if [ -n "$(git status --porcelain ledger)" ]; then
    git add ledger
    git commit -q -m "ledger: update $(date -u +%Y-%m-%dT%H:%MZ)"
    echo "ledger changes committed"
  fi
fi

if [ "${AUTO_DEPLOY:-0}" = "1" ]; then
  ./deploy.sh
fi
