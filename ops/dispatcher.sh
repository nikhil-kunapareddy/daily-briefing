#!/usr/bin/env bash
# Wakes at most one agent per run, based on GitHub labels. No model calls here: plain rules only.
# Runs every 5 minutes from the agents user's crontab (set up by agents/setup.sh). Rules: agents/WORKFLOW.md.
# Logs: ~/work/.dispatcher/dispatch.log (one line per decision), runs.jsonl (model and tokens per run), runs/ (raw output).
#
#   ops/dispatcher.sh            normal run
#   DRY_RUN=1 ops/dispatcher.sh  print what would happen, change nothing
#   touch ~/work/.dispatcher/PAUSE   stop all wake-ups until the file is removed (dry runs still work)
set -euo pipefail

REPO=nikhil-kunapareddy/daily-briefing
ROOT=/srv/daily-briefing                       # main-only checkout: prompts, lessons, this script
WORK=/home/agents/work
STATE=$WORK/.dispatcher
RUN_TIMEOUT=${RUN_TIMEOUT:-1200}               # seconds per agent run; neither runtime has its own cap
DRY_RUN=${DRY_RUN:-0}
BUILDERS=(builder-ingest builder-web tester)   # agents that take assign:<agent> issues and fix sent-back PRs
declare -A RUNTIME=(
  [orchestrator]=hermes [reviewer]=hermes
  [builder-ingest]=openclaw [builder-web]=openclaw [tester]=openclaw
)

# shellcheck source=../agents/env.sh
. "$ROOT/agents/env.sh"                        # PATH, keys, tracing names (cron starts with a bare environment)
mkdir -p "$STATE/runs" "$STATE/fails"
exec 9>"$STATE/lock"
flock -n 9 || exit 0                           # an agent is still running
[ -e "$STATE/PAUSE" ] && [ "$DRY_RUN" != 1 ] && exit 0   # a dry run still shows what would happen

now() { date -u +%FT%TZ; }
log() { echo "$(now) $*" >> "$STATE/dispatch.log"; [ "$DRY_RUN" = 1 ] && echo "$*" || true; }
act() { if [ "$DRY_RUN" = 1 ]; then echo "would: $*"; else "$@"; fi; }   # for changes on GitHub

# ---------------------------------------------------------------- board

prs=$(gh pr list -R "$REPO" --state open --limit 100 \
  --json number,headRefName,headRefOid,labels,createdAt,statusCheckRollup)
issues=$(gh issue list -R "$REPO" --state open --limit 200 --json number,labels,createdAt)

# Open PRs with label $1 and without needs-human, oldest first: "number branch sha" per line.
prs_with() {
  jq -r --arg l "$1" '[.[] | select(any(.labels[]; .name == $l)) | select(all(.labels[]; .name != "needs-human"))]
    | sort_by(.createdAt)[] | "\(.number) \(.headRefName) \(.headRefOid)"' <<<"$prs"
}

# Open issues with label $1 and without needs-human, oldest first: one number per line.
issues_with() {
  jq -r --arg l "$1" '[.[] | select(any(.labels[]; .name == $l)) | select(all(.labels[]; .name != "needs-human"))]
    | sort_by(.createdAt)[] | .number' <<<"$issues"
}

# CI ("pytest" check) on PR $1: success | failure | pending.
ci_state() {
  jq -r --argjson n "$1" '.[] | select(.number == $n) | [.statusCheckRollup[]? | select(.name == "pytest")]
    | if length == 0 then "pending" else (.[0]
      | if .status != "COMPLETED" then "pending" elif .conclusion == "SUCCESS" then "success" else "failure" end) end' <<<"$prs"
}

# Does PR $1 have a commit status with context $2?
has_status() {
  jq -e --argjson n "$1" --arg c "$2" '.[] | select(.number == $n) | any(.statusCheckRollup[]?; .context == $c)' \
    <<<"$prs" >/dev/null
}

# How many times PR $1 has been sent back.
sendbacks() {
  gh api "repos/$REPO/issues/$1/timeline" --paginate \
    --jq '.[] | select(.event == "labeled" and .label.name == "changes-requested") | 1' | wc -l
}

# Does agent $1 have an open PR for issue $2? (branch <agent>/<issue>-...)
has_open_pr() {
  jq -e --arg p "$1/$2-" 'any(.[]; .headRefName | startswith($p))' <<<"$prs" >/dev/null
}

relabel() { act gh pr edit "$1" -R "$REPO" --add-label "$2" --remove-label "$3" >/dev/null; }
comment() { act gh pr comment "$1" -R "$REPO" --body "$2"$'\n\n'"— dispatcher · no model" >/dev/null; }

# ---------------------------------------------------------------- waking an agent

# wake <agent> <key> <task line>. key names the issue or PR, e.g. pr-12, issue-7, board.
wake() {
  local agent=$1 key=$2 task=$3
  local stamp msg out rc start secs usage before model backup=""
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  msg=$STATE/runs/$stamp-$agent-$key.msg.md
  out=$STATE/runs/$stamp-$agent-$key.out       # stdout: the runtime's JSON. stderr goes to .err

  # A retry after a failed run uses the agent's first backup model, so the same model glitch doesn't repeat.
  if [ -e "$STATE/fails/$agent-$key" ]; then backup=$(backup_of "$agent"); fi
  if [ -n "$backup" ]; then model=$(short_model "${backup##* }"); else model=$(model_of "$agent"); fi
  {
    echo "Wake-up from the dispatcher, $(now)."
    echo
    echo "Task: $task"
    echo "Repo: $REPO. Read the details yourself with gh. Team rules: WORKFLOW.md."
    echo
    echo "You are $agent, running on $model. Sign everything you write on GitHub (WORKFLOW.md, Signatures):"
    echo "- last line of every PR body, issue and comment: — $agent · $model"
    echo "- commit trailers: Agent: $agent and Model: $model"
    case $agent in builder-*|tester)
      echo
      echo "Lessons from earlier rejections (agents/LESSONS.md):"
      echo
      cat "$ROOT/agents/LESSONS.md"
    esac
  } > "$msg"

  log "wake $agent $key: $task${backup:+ (retry on backup model $backup)}"
  [ "$DRY_RUN" = 1 ] && return 0

  bash "$ROOT/agents/sync-prompts.sh" "$agent"  # merged prompt changes take effect on this run
  before=$(item_state "$key")
  start=$(date +%s)
  set +e
  run_agent "$agent" "$key-$stamp" "$msg" "$backup" > "$out" 2> "${out%.out}.err"
  rc=$?
  set -e
  secs=$(( $(date +%s) - start ))
  usage=$(run_usage "$agent" "$out"); [ -n "$usage" ] || usage='{}'
  # Clean exits that still count as failures:
  # - no tool calls on an issue or PR: the agent can't have checked or changed anything (a made-up answer,
  #   or a tool call written as text);
  # - nothing changed on its issue or PR: no label, commit, comment or PR.
  if [ "$rc" -eq 0 ] && [[ $key =~ ^(pr|issue)-[0-9]+$ ]] && [ "$(jq -r '.tools // 1' <<<"$usage")" = 0 ]; then
    rc=4
    log "no tool calls: $agent on $key"
  elif [ "$rc" -eq 0 ] && [ -n "$before" ] && [ "$before" = "$(item_state "$key")" ]; then
    rc=3
    log "no progress: $agent left $key unchanged"
  fi
  log "done $agent $key exit=$rc secs=$secs $(jq -r '"model=\(.model // "?") in=\(.input // "?") out=\(.output // "?")"' <<<"$usage")"
  jq -c --arg t "$(now)" --arg a "$agent" --arg k "$key" --arg r "${RUNTIME[$agent]}" --argjson rc "$rc" --argjson s "$secs" \
    '{time: $t, agent: $a, runtime: $r, key: $k, exit: $rc, secs: $s} + .' <<<"$usage" >> "$STATE/runs.jsonl"

  # Two failed runs in a row on the same item: stop retrying and hand it to the human.
  if [ "$rc" -eq 0 ]; then
    rm -f "$STATE/fails/$agent-$key"
  elif [ "$(( $(cat "$STATE/fails/$agent-$key" 2>/dev/null || echo 0) + 1 ))" -ge 2 ]; then
    rm -f "$STATE/fails/$agent-$key"
    local n=${key#*-}
    if [[ $key =~ ^(pr|issue)-[0-9]+$ ]]; then   # the issues API covers PRs too
      gh api "repos/$REPO/issues/$n/labels" -f "labels[]=needs-human" >/dev/null
      gh api "repos/$REPO/issues/$n/comments" \
        -f body="Dispatcher: $agent failed twice on this (last exit code $rc). Needs a human. Log: \`$out\` on the VM."$'\n\n'"— dispatcher · no model" >/dev/null
    fi
    log "needs-human $key after 2 failed runs of $agent"
  else
    echo 1 > "$STATE/fails/$agent-$key"
  fi
}

# model_of <agent>: its configured main model, without the provider, for the wake-up message and signatures.
model_of() {
  local m
  case ${RUNTIME[$1]} in
    openclaw) m=$(cd "$STATE" && openclaw config get "agents.entries.$1.model.primary" 2>/dev/null | tail -1) ;;
    hermes) m=$(hermes -p "$1" config get model.default 2>/dev/null) ;;
  esac
  short_model "$m"
}

# short_model <ref>: the model name without its provider: ollama-cloud/gpt-oss:120b -> gpt-oss:120b.
short_model() { echo "$1" | sed -E 's#^(ollama-cloud|google|gemini)/##'; }

# backup_of <agent>: the agent's first fallback model, in the form run_agent takes.
backup_of() {
  case ${RUNTIME[$1]} in
    openclaw) (cd "$STATE" && openclaw config get "agents.entries.$1.model.fallbacks" 2>/dev/null) \
                | sed -n '/^\[/,/^\]/p' | jq -r '.[0] // empty' ;;
    hermes) local p m
            p=$(hermes -p "$1" config get fallback_providers.0.provider 2>/dev/null) || true
            m=$(hermes -p "$1" config get fallback_providers.0.model 2>/dev/null) || true
            [ -z "$p" ] || [ -z "$m" ] || echo "$p $m" ;;
  esac
}

# item_state <key>: what a useful run on pr-N or issue-N changes, as one line. Empty for other keys.
#   PR: labels, latest commit, comment count. Issue: labels, state, comment count, PRs whose branch names it.
item_state() {
  local n=${1#*-}
  case $1 in
    pr-*) gh pr view "$n" -R "$REPO" --json labels,headRefOid,comments \
            --jq '[([.labels[].name] | sort), .headRefOid, (.comments | length)] | tostring' 2>/dev/null || true ;;
    issue-*) echo "$(gh issue view "$n" -R "$REPO" --json labels,state,comments \
                --jq '[([.labels[].name] | sort), .state, (.comments | length)] | tostring' 2>/dev/null || true)" \
              "$(gh pr list -R "$REPO" --state all --limit 100 --json headRefName \
                --jq "[.[] | select(.headRefName | test(\"/$n-\"))] | length" 2>/dev/null || true)" ;;
  esac
}

# run_agent <agent> <session> <message file> [backup model]: one headless run, output to stdout.
# A backup model replaces the agent's own for this run: "provider/model" (OpenClaw) or "provider model" (Hermes).
run_agent() {
  local agent=$1 session=$2 msg=$3 backup=${4:-}
  local pick=()
  case ${RUNTIME[$agent]} in
    openclaw) [ -z "$backup" ] || pick=(--model "$backup") ;;
    hermes) [ -z "$backup" ] || pick=(--provider "${backup%% *}" -m "${backup##* }") ;;
  esac
  case ${RUNTIME[$agent]} in
    openclaw)
      # Started from the state folder: OpenClaw reads a .env from its working folder, and a clone could hold one.
      # Its tools run in the agent's clone (cwd in its config).
      ( cd "$STATE" &&
        timeout -k 60 "$RUN_TIMEOUT" openclaw agent --local --agent "$agent" --session-key "$session" \
          --message-file "$msg" --timeout $(( RUN_TIMEOUT - 60 )) "${pick[@]}" --json ) ;;
    hermes)
      # Hermes clones stay on main: Hermes reads instruction files (AGENTS.md, HERMES.md) from the working tree,
      # so it must never run inside a PR branch.
      local repo=$WORK/$agent/repo
      git -C "$repo" fetch -q origin main
      git -C "$repo" checkout -q -f main && git -C "$repo" reset -q --hard origin/main && git -C "$repo" clean -fdq
      ( cd "$repo" &&
        timeout -k 60 "$RUN_TIMEOUT" hermes -p "$agent" chat --oneshot -Q --run-budget $(( RUN_TIMEOUT - 60 )) \
          "${pick[@]}" --query-file "$msg" --format stream-json ) ;;
  esac
}

# run_usage <agent> <output file>: {provider, model, input, output, fallbacks, tools} as JSON, {} if unreadable.
# tools = number of tool calls that ran (OpenClaw: distinct tools used; Hermes: tool calls).
# Hermes reports only the requested model; a fallback shows up in its text output, not here.
run_usage() {
  case ${RUNTIME[$1]} in
    openclaw) sed -n '/^{/,$p' "$2" | jq -c '.meta.agentMeta // {} | {provider, model,
                input: .usage.input, output: .usage.output, fallbacks: (.fallbackAttempts // [] | length),
                tools: (.terminalReceipt.successfulToolNames // [] | map(select(. != "tool_search")) | length)}' ;;
    hermes) jq -cRn '[inputs | fromjson? // empty] as $l
              | ($l | map(select(.type == "system")) | first // {}) as $init
              | ($l | map(select(.type == "result")) | last // {}) as $r
              | {model: $init.model, input: $r.tokens.input, output: $r.tokens.output,
                 tools: ($l | map(select(.type == "tool_use")) | length)}' "$2" ;;
  esac 2>/dev/null || echo '{}'
}

# ---------------------------------------------------------------- 1. reviewer

while read -r n branch sha; do
  [ -n "$n" ] || continue
  if [[ $branch == tester/* ]] && ! has_status "$n" test/passed; then
    act gh api "repos/$REPO/statuses/$sha" -f state=success -f context=test/passed \
      -f description="Tester's own PR: not self-tested. CI and the reviewer gate it." >/dev/null
  fi
  wake reviewer "pr-$n" "PR #$n needs review."
  exit 0
done < <(prs_with needs-review)

# ---------------------------------------------------------------- 2. tester (CI first)

while read -r n branch sha; do
  [ -n "$n" ] || continue
  case $(ci_state "$n") in
    success) wake tester "pr-$n" "PR #$n needs testing."; exit 0 ;;
    failure)
      relabel "$n" changes-requested needs-test
      comment "$n" "Dispatcher: CI (pytest) failed on this PR, so it goes back to the builder without testing. See the checks tab."
      log "sent back pr-$n: CI failed"
      prs=$(gh pr list -R "$REPO" --state open --limit 100 \
        --json number,headRefName,headRefOid,labels,createdAt,statusCheckRollup) ;;
    pending) ;;
  esac
done < <(prs_with needs-test)

# ---------------------------------------------------------------- 3. builders: sent-back PRs, then new issues

while read -r n branch sha; do
  [ -n "$n" ] || continue
  owner=${branch%%/*}
  [[ " ${BUILDERS[*]} " == *" $owner "* ]] || { log "skip pr-$n: branch $branch has no builder prefix"; continue; }
  if [ "$(sendbacks "$n")" -gt 2 ]; then
    relabel "$n" needs-human changes-requested
    comment "$n" "Dispatcher: sent back 3 times. Over to the human (WORKFLOW.md rule 5)."
    log "needs-human pr-$n after 3 send-backs"
    continue
  fi
  wake "$owner" "pr-$n" "PR #$n was sent back. Fix it."
  exit 0
done < <(prs_with changes-requested)

for agent in "${BUILDERS[@]}"; do
  for n in $(issues_with "assign:$agent"); do
    has_open_pr "$agent" "$n" && continue
    wake "$agent" "issue-$n" "Issue #$n is assigned to you."
    exit 0
  done
done

# ---------------------------------------------------------------- 4. orchestrator: daily lessons

# Once a day, collect the Lesson: lines (at the start of a line) posted since the last collection. The orchestrator
# folds them into agents/LESSONS.md in a PR labelled lessons, which the human merges. Skipped while one is open.
today=$(date -u +%F)
if [ "$(cat "$STATE/lessons-day" 2>/dev/null)" != "$today" ] && [ -z "$(prs_with lessons)" ]; then
  since=$(cat "$STATE/lessons-since" 2>/dev/null || date -u -d '1 day ago' +%FT%TZ)
  lessons=$(gh api "repos/$REPO/issues/comments?since=$since&per_page=100" --paginate \
    --jq '.[] | .html_url as $u | .body | scan("(?m)^Lesson:.*$") | "- \(.) (\($u))"')
  if [ "$DRY_RUN" != 1 ]; then echo "$today" > "$STATE/lessons-day"; now > "$STATE/lessons-since"; fi
  if [ -n "$lessons" ]; then
    wake orchestrator lessons "Collect lessons. These Lesson: lines were posted since $since:
$lessons"
    exit 0
  fi
fi

# ---------------------------------------------------------------- 5. orchestrator: the board

board() {
  gh issue list -R "$REPO" --state all --limit 300 --json number,state,labels \
    --jq 'map({n: .number, s: .state, l: ([.labels[].name] | sort)}) | sort_by(.n)'
}
save_board() { [ "$DRY_RUN" = 1 ] || board > "$STATE/board.json"; }

current=$(board)

# An assign:orchestrator issue wakes it once. If the label is still there afterwards, only a board change wakes it again.
for n in $(issues_with assign:orchestrator); do
  if [ -e "$STATE/handled-issue-$n" ] && [ "$current" = "$(cat "$STATE/board.json" 2>/dev/null)" ]; then
    continue
  fi
  wake orchestrator "issue-$n" "Issue #$n is assigned to you."
  [ "$DRY_RUN" = 1 ] || touch "$STATE/handled-issue-$n"
  save_board
  exit 0
done

if [ ! -s "$STATE/board.json" ]; then
  save_board                                   # first run: nothing to compare with
  exit 0
fi
changes=$(jq -rn --argjson old "$(cat "$STATE/board.json")" --argjson new "$current" '
  ($old | map({key: (.n | tostring), value: .}) | from_entries) as $o
  | [ $new[] | . as $i | $o[$i.n | tostring] as $p
      | if $p == null then "new issue #\($i.n) (labels: \($i.l | join(", ")))"
        elif $p.s != $i.s then "#\($i.n) is now \($i.s | ascii_downcase)"
        elif $p.l != $i.l then "#\($i.n) labels are now: \($i.l | join(", "))"
        else empty end ]
  | join("; ")')
if [ -n "$changes" ]; then
  wake orchestrator board "The board changed since your last run: $changes."
  save_board
fi
