#!/usr/bin/env bash
# Deploy ./site to Cloudflare with wrangler. Cloudflare Pages now runs on Workers: the site is a
# static-assets Worker described by wrangler.jsonc (name = project, assets = ./site; _headers and
# _redirects are honoured). First run: asks for a project name once, saves it to .cloudflare-project.
# Auth: interactive runs can use `wrangler login` (browser). Unattended runs use CLOUDFLARE_API_TOKEN
#   ("Edit Cloudflare Workers" template) and CLOUDFLARE_ACCOUNT_ID, from the environment or .env.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
CONF=.cloudflare-project

# Unattended deploys (launchd/cron): read the Cloudflare credentials from .env (gitignored) unless
# they are already in the environment. Only these two keys are read; .env is never sourced.
for k in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID; do
  if [ -z "${!k:-}" ] && [ -f .env ]; then
    v="$(grep -E "^$k=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"'[:space:]')"
    [ -n "$v" ] && export "$k=$v"
  fi
done
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
  echo "$PROJECT" > "$CONF"
  echo "Saved project name to $CONF"
fi

$WRANGLER deploy --name "$PROJECT"
