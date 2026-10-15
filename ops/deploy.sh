#!/usr/bin/env bash
# Keeps /srv/daily-briefing on the latest main, and re-runs the app's install script after each change.
# Runs every 2 minutes as the app user (cron, set up by ops/setup-vm.sh). Pull-based: no SSH key or deploy secret in GitHub.
set -euo pipefail

export PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
export XDG_RUNTIME_DIR=/run/user/$(id -u)      # lets install.sh use `systemctl --user` from cron (lingering is on)

cd /srv/daily-briefing
exec 9>/tmp/daily-briefing-deploy.lock
flock -n 9 || exit 0

git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
git reset -q --hard origin/main                # main-only checkout: nothing local to keep

umask 022
log=/var/log/daily-briefing/deploy.log          # readable by the agents (builder-ops), unlike the data folder
echo "$(date -u +%FT%TZ) deployed $(git rev-parse --short HEAD)" >> "$log"
install=none
if [ -f scripts/install.sh ]; then             # written by the agents; must be safe to re-run
  set +e; bash scripts/install.sh >> "$log" 2>&1; rc=$?; set -e
  if [ "$rc" = 0 ]; then install=ok; else install="failed (exit $rc)"; echo "$(date -u +%FT%TZ) install.sh $install" >> "$log"; fi
fi
# What the dispatcher's works-in-main check reads: which commit is live, and whether its install worked.
printf '{"sha": "%s", "time": "%s", "install": "%s"}\n' "$(git rev-parse HEAD)" "$(date -u +%FT%TZ)" "$install" \
  > /var/log/daily-briefing/deploy-status.json.tmp
mv /var/log/daily-briefing/deploy-status.json.tmp /var/log/daily-briefing/deploy-status.json
