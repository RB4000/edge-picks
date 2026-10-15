#!/usr/bin/env bash
# Idempotent: refresh data, capture/freeze lines, lock picks, regrade, rebuild ./site.
# Safe to run repeatedly or from cron; a file lock prevents overlapping runs.
# Extra args go to the pipeline, e.g. ./update.sh --verbose
#   AUTO_DEPLOY=1        also deploy to Cloudflare Pages after a successful update
#   LEDGER_AUTOCOMMIT=1  git-commit any ledger changes and push them to origin (GitHub's push
#                        timestamps are the outside record that picks existed before kickoff)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p data/logs

if [ ! -x .venv/bin/python ]; then
  echo "No virtualenv found. Run: make setup" >&2
  exit 1
fi

echo "=== update $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
trap 'rc=$?; [ $rc -ne 0 ] && echo "[$(date +%H:%M:%S)] UPDATE FAILED (exit $rc): ledger not committed, site not deployed" >&2' EXIT

.venv/bin/python -m nfledge.pipeline update "$@"

PUSH=skipped
if [ "${LEDGER_AUTOCOMMIT:-0}" = "1" ] && [ -d .git ]; then
  if [ -n "$(git status --porcelain ledger)" ]; then
    git add ledger
    git commit -q -m "ledger: update $(date -u +%Y-%m-%dT%H:%MZ)"
    echo "ledger changes committed"
  fi
  if git remote get-url origin >/dev/null 2>&1; then
    if git push -q origin HEAD 2>&1; then
      echo "pushed to origin"; PUSH=ok
    else
      echo "WARNING: git push failed; ledger committed locally only" >&2; PUSH=failed
    fi
  fi
fi

# Heartbeat for the off-machine watchdog (.github/workflows/watchdog.yml). Written only after a successful
# pipeline run, deployed with the site; the watchdog fails if it goes stale or reports a failed push.
printf '{"updated_at": "%s", "commit": "%s", "ledger_push": "%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse --short HEAD 2>/dev/null || echo none)" "$PUSH" > data/status.json
cp data/status.json site/status.json
echo "[$(date +%H:%M:%S)] heartbeat written (ledger push: $PUSH)"

if [ "${AUTO_DEPLOY:-0}" = "1" ]; then
  ./deploy.sh
fi
