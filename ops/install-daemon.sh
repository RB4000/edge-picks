#!/usr/bin/env bash
# Move the daily update from a per-user LaunchAgent (runs only while you're logged in) to a system LaunchDaemon
# (runs at 7:00 even at the login window). Run as yourself from the repo root, NOT with sudo; it asks for your
# password once for the steps that need it. Safe to re-run.
#   ./ops/install-daemon.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
LABEL=com.nfledge.update
DEST=/Library/LaunchDaemons/$LABEL.plist
KEY="$HOME/.ssh/edgepicks_deploy"
SLUG="$(git remote get-url origin | sed -E 's#(https://github.com/|git@github.com:)##; s#\.git$##')"

[ "$(id -u)" -ne 0 ] || { echo "Run as yourself, not with sudo." >&2; exit 1; }

echo "1/5 deploy key for unattended git push ($SLUG)"
if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "edge-picks launchd deploy key" -f "$KEY"
fi
if ! gh repo deploy-key list --repo "$SLUG" | grep -q "edge-picks launchd"; then
  gh repo deploy-key add "$KEY.pub" --repo "$SLUG" --allow-write --title "edge-picks launchd"
fi
git remote set-url --push origin "git@github.com:$SLUG.git"
git config core.sshCommand "ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git push --dry-run -q origin HEAD && echo "   push over deploy key: ok"

echo "2/5 remove the per-user LaunchAgent (so the job never runs twice)"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"

echo "3/5 install $DEST (needs your password)"
TMP="$(mktemp)"
sed -e "s|/path/to/nfl-edge|$REPO|g" -e "s|__USER__|$(id -un)|g" -e "s|__HOME__|$HOME|g" \
  ops/$LABEL.daemon.plist > "$TMP"
plutil -lint "$TMP" >/dev/null
sudo install -o root -g wheel -m 644 "$TMP" "$DEST"
rm -f "$TMP"

echo "4/5 load it"
sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo launchctl bootstrap system "$DEST"

echo "5/5 test run now, exactly as 7:00 will run it (takes a few minutes)"
before=$(wc -l < data/logs/update.log)
sudo launchctl kickstart -k "system/$LABEL"
sleep 5
until sudo launchctl print "system/$LABEL" | grep -q "state = not running"; do sleep 5; done
tail -n +$((before + 1)) data/logs/update.log | grep -vE '^\+ /|Uploaded [0-9]+ of'
sudo launchctl print "system/$LABEL" | grep -E "runs =|last exit code"
if tail -n +$((before + 1)) data/logs/update.log | grep -q "heartbeat written (ledger push: ok)"; then
  echo "INSTALLED: daemon ran, pushed and wrote the heartbeat. Next run: 7:00 tomorrow."
else
  echo "PROBLEM: the test run didn't finish with a successful push. See the log above." >&2
  exit 1
fi
