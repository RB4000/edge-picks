#!/usr/bin/env bash
# Deploy ./site to Cloudflare Pages with wrangler.
# First run: asks for a project name once, saves it to .cloudflare-project, creates the project.
# Auth: interactive runs use `wrangler login` (browser). For cron/unattended deploys, export
#   CLOUDFLARE_API_TOKEN (Pages:Edit permission) and CLOUDFLARE_ACCOUNT_ID.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
CONF=.cloudflare-project
WRANGLER="npx --yes wrangler@4"

[ -f site/index.html ] || { echo "site/ not built yet. Run: make update" >&2; exit 1; }

if [ -f "$CONF" ]; then
  PROJECT="$(tr -d '[:space:]' < "$CONF")"
else
  if [ ! -t 0 ]; then
    echo "No $CONF and not interactive. Run ./deploy.sh once by hand to choose a project name." >&2
    exit 1
  fi
  read -rp "Cloudflare Pages project name (lowercase, digits, hyphens; becomes <name>.pages.dev): " PROJECT
  if ! [[ "$PROJECT" =~ ^[a-z0-9][a-z0-9-]{0,56}[a-z0-9]$ ]]; then
    echo "Invalid name: use lowercase letters, digits and hyphens." >&2
    exit 1
  fi
  if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    $WRANGLER whoami >/dev/null 2>&1 || $WRANGLER login
  fi
  if ! $WRANGLER pages project list 2>/dev/null | grep -qw "$PROJECT"; then
    $WRANGLER pages project create "$PROJECT" --production-branch main
  fi
  echo "$PROJECT" > "$CONF"
  echo "Saved project name to $CONF"
fi

$WRANGLER pages deploy site --project-name "$PROJECT" --branch main --commit-dirty=true
