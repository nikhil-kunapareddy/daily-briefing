#!/usr/bin/env bash
# VM setup that needs the keys, so it runs after infra/push-secrets.sh copies them in (push-secrets.sh calls it).
# Root. Safe to re-run.
#
#   app     ops/deploy.sh every 2 minutes, and lingering, so the app's systemd --user services run without a login
#   agents  agents/setup.sh (runtimes, profiles, clones), then ops/dispatcher.sh --chain every 2 minutes
#
# The dispatcher starts paused. To start the team:  sudo -u agents rm /home/agents/work/.dispatcher/PAUSE
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

ROOT=/srv/daily-briefing
STATE=/home/agents/work/.dispatcher

# cron_line <user> <tag> <line>: one crontab line per tag, replaced on re-run.
cron_line() {
  { crontab -u "$1" -l 2>/dev/null | grep -v "# $2\$" || true; echo "$3 # $2"; } | crontab -u "$1" -
  echo "  crontab $1: $2"
}
has_cron() { crontab -u "$1" -l 2>/dev/null | grep -q "# $2\$"; }

echo "== app"
loginctl enable-linger app
install -d -o app -g app -m 0755 /var/log/daily-briefing   # deploy.log: readable by the agents, unlike the data
cron_line app deploy "*/2 * * * * bash $ROOT/ops/deploy.sh"

echo "== agents"
sudo -u agents -H bash "$ROOT/agents/setup.sh"
install -d -o agents -g agents -m 0750 "$STATE"
if ! has_cron agents dispatcher; then           # first install only: the human starts the team
  sudo -u agents touch "$STATE/PAUSE"
  echo "  dispatcher paused (remove $STATE/PAUSE to start)"
fi
cron_line agents dispatcher "*/2 * * * * bash $ROOT/ops/dispatcher.sh --chain >> $STATE/cron.log 2>&1"
echo "== done"
