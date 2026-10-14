#!/usr/bin/env bash
# First-boot setup for the daily-briefing VM. cloud-init runs it once, as root.
# Log: /var/log/first-boot.log. Safe to re-run: every step checks before it changes anything.
#
# Leaves the VM with:
#   users     agents (runs OpenClaw + Hermes, no sudo)   app (runs the product, owns its data)
#   code      /srv/daily-briefing      main-only checkout, owned by app, read-only for agents
#   data      /var/lib/daily-briefing  owned by app, closed to agents
#   web       Caddy: https://<duckdns>.duckdns.org -> 127.0.0.1:8000
#   runtimes  Node 24, OpenClaw (pinned), Hermes Agent (pinned), gh
#
# Not done here: keys (infra/push-secrets.sh copies them in after boot), agent profiles (agents/),
# the app itself (scripts/install.sh, written by the agents).
set -euxo pipefail

# shellcheck source=/dev/null
. /opt/first-boot/config.env
export DEBIAN_FRONTEND=noninteractive

timedatectl set-timezone Etc/UTC

# ---------------------------------------------------------------- users and folders

id agents >/dev/null 2>&1 || useradd --create-home --shell /bin/bash agents
id app >/dev/null 2>&1 || useradd --create-home --shell /bin/bash app

install -d -o app -g app -m 0755 /srv/daily-briefing
install -d -o app -g app -m 0750 /var/lib/daily-briefing
install -d -o agents -g agents -m 0750 /home/agents/work

# ---------------------------------------------------------------- VM firewall
# Oracle's Ubuntu image ends INPUT with a REJECT rule. Insert 80/443 above it.

for port in 80 443; do
  if ! iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    reject_line=$(iptables -L INPUT --line-numbers | awk '$2 == "REJECT" {print $1; exit}')
    iptables -I INPUT "${reject_line:-1}" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
command -v netfilter-persistent >/dev/null || apt-get install -y iptables-persistent
netfilter-persistent save

# ---------------------------------------------------------------- apt repos: Node, gh, Caddy

install -d -m 0755 /etc/apt/keyrings

curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
  | gpg --batch --yes --dearmor -o /etc/apt/keyrings/nodesource.gpg
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
  > /etc/apt/sources.list.d/nodesource.list

curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list

curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  -o /etc/apt/sources.list.d/caddy-stable.list

apt-get update
apt-get install -y nodejs gh caddy

# OpenClaw needs Node 24.16+ on the 24 line.
node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a===24 && b>=16 || a>=26 ? 0 : 1)' \
  || { echo "Node $(node --version) is too old for OpenClaw"; exit 1; }

# ---------------------------------------------------------------- code

if [ ! -d /srv/daily-briefing/.git ]; then
  sudo -u app git clone "$REPO_URL" /srv/daily-briefing
fi

# ---------------------------------------------------------------- web: Caddy (automatic HTTPS)
# The certificate is issued once DuckDNS points at this VM (after push-secrets.sh). Caddy retries until then.
# Until the app exists, the site answers 502.

cat > /etc/caddy/Caddyfile <<EOF
${DUCKDNS_DOMAIN}.duckdns.org {
	reverse_proxy 127.0.0.1:8000
}
EOF
systemctl enable caddy
systemctl restart caddy

# ---------------------------------------------------------------- DuckDNS: keep the name on this VM's IP

cat > /usr/local/bin/duckdns-update <<'EOF'
#!/usr/bin/env bash
# Reads /root/root.env (copied in by push-secrets.sh). Empty ip= lets DuckDNS use this VM's public IP.
set -euo pipefail
[ -r /root/root.env ] || exit 0
# shellcheck source=/dev/null
. /root/root.env
curl -fsS --max-time 30 "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=" \
  -o /var/log/duckdns.last
EOF
chmod 0755 /usr/local/bin/duckdns-update
echo "*/5 * * * * root /usr/local/bin/duckdns-update" > /etc/cron.d/duckdns

# ---------------------------------------------------------------- agent runtimes (pinned)

# OpenClaw: installed system-wide by root, so the agents user can run it but not modify or auto-update it.
# State lives in each user's ~/.openclaw.
if [ "$(openclaw --version 2>/dev/null || true)" != "$OPENCLAW_VERSION" ]; then
  npm install -g "openclaw@${OPENCLAW_VERSION}" --allow-scripts=openclaw
fi
grep -q '^OPENCLAW_NO_AUTO_UPDATE=' /etc/environment || echo 'OPENCLAW_NO_AUTO_UPDATE=1' >> /etc/environment

# Hermes: its installer manages its own Python (3.14, via uv) under ~agents/.hermes.
if [ ! -x /home/agents/.local/bin/hermes ]; then
  sudo -u agents -H bash -lc "curl -fsSL https://hermes-agent.nousresearch.com/install.sh \
    | bash -s -- --non-interactive --skip-browser --skip-computer-use --commit ${HERMES_COMMIT}"
fi

# ---------------------------------------------------------------- report

set +x
echo "== versions"
node --version
openclaw --version || echo "openclaw: NOT WORKING"
sudo -u agents -H /home/agents/.local/bin/hermes --version || echo "hermes: NOT WORKING"
gh --version | head -1
caddy version
python3 --version
date -u > /var/lib/first-boot.done
echo "== first boot finished"
