#!/usr/bin/env bash
# Retries `terraform apply` while Oracle reports "Out of host capacity" for A1 VMs.
# Stops on success or on any other error. Review `terraform plan` once before using this.
#   infra/apply-retry.sh [minutes-between-tries] [max-tries]      (default: 10, 36 = ~6 h)
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
wait_min=${1:-10}
max=${2:-36}
log="$here/apply-retry.log"

for try in $(seq 1 "$max"); do
  echo "== try $try/$max at $(date -u +%FT%TZ)" | tee -a "$log"
  if terraform -chdir="$here" apply -auto-approve -input=false 2>&1 | tee -a "$log"; then
    echo "== VM created" | tee -a "$log"
    exit 0
  fi
  if ! tail -n 50 "$log" | grep -q "Out of host capacity"; then
    echo "== failed for another reason; see $log" | tee -a "$log"
    exit 1
  fi
  echo "== no capacity; next try in $wait_min min" | tee -a "$log"
  sleep $((wait_min * 60))
done
echo "== gave up after $max tries" | tee -a "$log"
exit 1
