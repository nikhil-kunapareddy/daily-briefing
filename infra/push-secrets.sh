#!/usr/bin/env bash
# Copies the key files onto the VM after `terraform apply`. Run from your laptop.
#   infra/push-secrets.sh [secrets-dir]      (default: ../secrets, next to the repo)
#
# Each file lands readable only by its Linux user:
#   agents.env -> /home/agents/.env      app.env -> /home/app/.env      root.env -> /root/root.env
# Then: points DuckDNS at the VM, and stores the GitHub token in gh's login for the agents user
# (Hermes strips GH_TOKEN from agent tools, so a stored login is what reaches them).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
secrets=${1:-$here/../../secrets}
key=${SSH_KEY:-$HOME/.ssh/confer_vm}
ip=$(terraform -chdir="$here" output -raw public_ip)
ssh_vm() { ssh -i "$key" -o StrictHostKeyChecking=accept-new "ubuntu@$ip" "$@"; }

for f in agents.env app.env root.env; do
  [ -s "$secrets/$f" ] || { echo "missing or empty: $secrets/$f"; exit 1; }
done

echo "== waiting for first boot to finish on $ip"
ssh_vm 'cloud-init status --wait >/dev/null; test -f /var/lib/first-boot.done' \
  || { echo "first boot failed: ssh in and read /var/log/first-boot.log"; exit 1; }

put() { # put <local file> <owner> <remote path>
  ssh_vm "sudo bash -c 'umask 077; cat > $3; chown $2:$2 $3'" < "$1"
  echo "  $(basename "$1") -> $3"
}
echo "== copying keys"
put "$secrets/agents.env" agents /home/agents/.env
put "$secrets/app.env"    app    /home/app/.env
put "$secrets/root.env"   root   /root/root.env

echo "== DuckDNS"
ssh_vm 'sudo /usr/local/bin/duckdns-update && echo "  $(cat /var/log/duckdns.last)"'

echo "== GitHub login for agents"
ssh_vm 'sudo -u agents -H bash -s' <<'EOF'
set -e
token=$(grep '^GH_TOKEN=' ~/.env | cut -d= -f2-)
printf %s "$token" | env -u GH_TOKEN gh auth login --with-token
env -u GH_TOKEN gh auth setup-git
env -u GH_TOKEN gh api user --jq '"  signed in as \(.login)"'
EOF
echo "== done"
