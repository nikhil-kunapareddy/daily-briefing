#!/usr/bin/env bash
# Sets up the six agents for the agents user, from the files in agents/. Safe to re-run.
# ops/setup-vm.sh runs it; by hand:  sudo -u agents -H bash /srv/daily-briefing/agents/setup.sh
#
#   clones    ~/work/<agent>/repo, one per agent, commits authored as the agent's name on the machine account
#   OpenClaw  builder-ingest, builder-web, tester: openclaw/openclaw.json5, exec allowlist, Langfuse plugin
#   Hermes    orchestrator, reviewer, debt-lead: one profile each, settings from hermes/*.json, Langfuse plugin
#   prompts   sync-prompts.sh
set -euo pipefail

export ROOT=${ROOT:-/srv/daily-briefing}       # override only to try an unmerged copy of agents/
A=$ROOT/agents
WORK=/home/agents/work
REPO=nikhil-kunapareddy/daily-briefing
OPENCLAW_AGENTS=(builder-ingest builder-web tester)
HERMES_AGENTS=(orchestrator reviewer debt-lead)

# shellcheck source=env.sh
. "$A/env.sh"
cd "$WORK"                                     # neutral folder: OpenClaw reads a .env from its working folder

# ---------------------------------------------------------------- clones

echo "== clones"
login=$(env -u GH_TOKEN gh api user --jq .login)
email="$(env -u GH_TOKEN gh api user --jq .id)+$login@users.noreply.github.com"   # links commits to the account
for agent in "${OPENCLAW_AGENTS[@]}" "${HERMES_AGENTS[@]}"; do
  repo=$WORK/$agent/repo
  [ -d "$repo/.git" ] || git clone -q "https://github.com/$REPO.git" "$repo"
  git -C "$repo" config user.name "$agent"
  git -C "$repo" config user.email "$email"
  echo "  $agent: $repo ($agent <$email>)"
done

# ---------------------------------------------------------------- OpenClaw

echo "== OpenClaw"
mkdir -p ~/.openclaw
# The plugin first: our config enables it, and a config naming a missing plugin doesn't validate.
if ! openclaw plugins inspect diagnostics-otel >/dev/null 2>&1; then
  openclaw plugins install clawhub:@openclaw/diagnostics-otel
fi
cp "$A/openclaw/openclaw.json5" ~/.openclaw/openclaw.json
chmod 600 ~/.openclaw/openclaw.json

# Exec allowlist: one list for all three agents, AGENT replaced by each agent's name.
patterns=$(grep -v -e '^#' -e '^[[:space:]]*$' "$A/openclaw/exec-allowlist.txt" | jq -R . | jq -s .)
jq -n --argjson p "$patterns" '{version: 1, defaults: {},
  agents: ($ARGS.positional | map({key: ., value: {allowlist: (. as $a | $p | map({pattern: sub("AGENT"; $a)}))}})
           | from_entries)}' --args "${OPENCLAW_AGENTS[@]}" > ~/.openclaw/approvals.json
openclaw approvals set --file ~/.openclaw/approvals.json >/dev/null
openclaw config validate
echo "  allowlist: $(jq length <<<"$patterns") programs for ${OPENCLAW_AGENTS[*]}"

# ---------------------------------------------------------------- Hermes

echo "== Hermes"
for agent in "${HERMES_AGENTS[@]}"; do
  [ -d ~/.hermes/profiles/"$agent" ] ||
    hermes profile create "$agent" --no-alias --description "daily-briefing $agent" >/dev/null
  # Shared settings, then the agent's own (later keys win). Strings go in as they are, the rest as JSON.
  jq -r 'to_entries[] | [.key, (.value | if type == "string" then . else tojson end)] | @tsv' \
    "$A/hermes/common.json" "$A/hermes/$agent.json" |
  while IFS=$'\t' read -r key value; do
    hermes -p "$agent" config set "$key" "$value" >/dev/null
  done
  if ! hermes -p "$agent" plugins list --plain 2>/dev/null | grep -qw langfuse; then
    hermes -p "$agent" plugins install langfuse --yes-deps --enable >/dev/null
  fi
  echo "  $agent: $(hermes -p "$agent" config get model.provider)/$(hermes -p "$agent" config get model.default)"
done

# ---------------------------------------------------------------- prompts

echo "== prompts"
bash "$A/sync-prompts.sh"
echo "  copied for all six agents"
echo "== agents ready"
