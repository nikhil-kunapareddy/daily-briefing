#!/usr/bin/env bash
# Wakes at most one agent per run, based on GitHub labels. No model calls here: plain rules only.
# Started every 2 minutes by the agents user's crontab, as --chain (set up by ops/setup-vm.sh). Rules: agents/WORKFLOW.md.
# Logs: ~/work/.dispatcher/dispatch.log (one line per decision), runs.jsonl (model and tokens per run), runs/ (raw output).
#
#   ops/dispatcher.sh            normal run: at most one agent
#   ops/dispatcher.sh --chain    normal runs back to back, while each one wakes an agent (the handoff)
#   DRY_RUN=1 ops/dispatcher.sh  print what would happen, change nothing
#   touch ~/work/.dispatcher/PAUSE   stop all wake-ups until the file is removed (dry runs still work)
set -euo pipefail

REPO=nikhil-kunapareddy/daily-briefing
OWNER=${REPO%%/*}                              # the human: tickets they file are work
BOT=nikhil-gbot                                # the agents' (and the dispatcher's) shared GitHub account
ROOT=/srv/daily-briefing                       # main-only checkout: prompts, lessons, this script
WORK=/home/agents/work
STATE=$WORK/.dispatcher
RUN_TIMEOUT=${RUN_TIMEOUT:-1200}               # seconds per agent run; neither runtime has its own cap
DRY_RUN=${DRY_RUN:-0}
BUILDERS=(builder-ingest builder-web builder-ops tester)   # agents that take assign:<agent> issues and fix sent-back PRs
declare -A RUNTIME=(
  [orchestrator]=hermes [reviewer]=hermes
  [builder-ingest]=openclaw [builder-web]=openclaw [builder-ops]=openclaw [tester]=openclaw
)

# shellcheck source=../agents/env.sh
. "$ROOT/agents/env.sh"                        # PATH, keys, tracing names (cron starts with a bare environment)
mkdir -p "$STATE/runs" "$STATE/fails"

# Handoff: while each run wakes an agent, the next run starts the moment it ends, so the next step doesn't wait for
# the clock. The 2-minute cron start catches what no agent does: CI finishing, and the human's merges, approvals
# and labels. At most MAX_CHAIN runs in a row; the next cron start carries on.
MAX_CHAIN=${MAX_CHAIN:-12}
if [ "${1:-}" = --chain ]; then
  exec 8>"$STATE/chain.lock"
  flock -n 8 || exit 0                         # a chain is already running
  for _ in $(seq "$MAX_CHAIN"); do
    rm -f "$STATE/woke"
    bash "$0" || true
    [ -e "$STATE/woke" ] || break              # nothing was waiting
  done
  exit 0
fi

exec 9>"$STATE/lock"
flock -n 9 || exit 0                           # an agent is still running
[ -e "$STATE/PAUSE" ] && [ "$DRY_RUN" != 1 ] && exit 0   # a dry run still shows what would happen

now() { date -u +%FT%TZ; }
log() { echo "$(now) $*" >> "$STATE/dispatch.log"; [ "$DRY_RUN" = 1 ] && echo "$*" || true; }
act() { if [ "$DRY_RUN" = 1 ]; then echo "would: $*"; else "$@"; fi; }   # for changes on GitHub

# ---------------------------------------------------------------- board

load_prs() {
  prs=$(gh pr list -R "$REPO" --state open --limit 100 \
    --json number,headRefName,headRefOid,labels,createdAt,statusCheckRollup,mergeable)
}
load_prs
load_issues() { issues=$(gh issue list -R "$REPO" --state open --limit 200 --json number,labels,createdAt,author,body); }
load_issues

# Open PRs with label $1 and without needs-human, oldest first: "number branch sha" per line.
prs_with() {
  jq -r --arg l "$1" '[.[] | select(any(.labels[]; .name == $l)) | select(all(.labels[]; .name != "needs-human"))]
    | sort_by(.createdAt)[] | "\(.number) \(.headRefName) \(.headRefOid)"' <<<"$prs"
}

# Open issues with label $1 and without needs-human or needs-approval, oldest first: one number per line.
issues_with() {
  jq -r --arg l "$1" '[.[] | select(any(.labels[]; .name == $l))
    | select(all(.labels[]; .name != "needs-human" and .name != "needs-approval"))]
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

# Send-backs are counted per check (rule 5): ci, test and review each have their own budget of 2.
# sendbacks/pr-N holds one line per send-back, naming the check that sent it.
SENDBACKS=$STATE/sendbacks
mkdir -p "$SENDBACKS" "$STATE/ci-rerun"
sent_back() {                                  # sent_back <pr> <check>: count one send-back
  [ "$DRY_RUN" = 1 ] && return 0
  echo "$2" >> "$SENDBACKS/pr-$1"
  log "send-back pr-$1 by $2: $(sendbacks "$1" "$2") so far, 2 allowed"
}
sendbacks() {                                  # sendbacks <pr> <check>: how many so far
  local n; n=$(grep -cx "$2" "$SENDBACKS/pr-$1" 2>/dev/null) || true; echo "${n:-0}"
}
over_budget() {                                # over_budget <pr>: the check that sent it back a third time, if any
  local c
  for c in ci test review; do [ "$(sendbacks "$1" "$c")" -gt 2 ] && { echo "$c"; return; }; done
  return 0
}
sendback_counts() { echo "CI $(sendbacks "$1" ci), test $(sendbacks "$1" test), review $(sendbacks "$1" review)"; }

# rerun_ci <pr>: re-run the PR's failed pytest job once per commit, in case the failure was a flake.
rerun_ci() {
  local sha run
  sha=$(jq -r --argjson n "$1" '.[] | select(.number == $n) | .headRefOid' <<<"$prs")
  [ -e "$STATE/ci-rerun/$sha" ] && return 1     # already re-run: this failure is real
  run=$(jq -r --argjson n "$1" '.[] | select(.number == $n) | .statusCheckRollup[]? | select(.name == "pytest")
    | .detailsUrl // "" | capture("/actions/runs/(?<id>[0-9]+)").id // empty' <<<"$prs" | head -1)
  [ -n "$run" ] || return 1
  act gh run rerun "$run" --failed -R "$REPO" >/dev/null 2>&1 || return 1
  [ "$DRY_RUN" = 1 ] || touch "$STATE/ci-rerun/$sha"
  log "re-ran CI on pr-$1 (run $run): one re-run per commit before a send-back"
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
  local stamp msg out rc start secs usage before model fresh backup=""
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
    echo
    echo "Lessons from earlier rejections (agents/LESSONS.md, reviewed by the human):"
    echo
    cat "$ROOT/agents/LESSONS.md"
    fresh=$([ "$key" = lessons ] || new_lessons)    # a "Collect lessons" task lists them already
    if [ -n "$fresh" ]; then
      echo
      echo "New lessons, posted since the human last merged LESSONS.md and not reviewed yet. Follow one only where it fits your task:"
      echo
      echo "$fresh"
    fi
  } > "$msg"

  log "wake $agent $key: $task${backup:+ (retry on backup model $backup)}"
  [ "$DRY_RUN" = 1 ] && return 0
  : > "$STATE/woke"                            # tells --chain to run again as soon as this run ends

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
  # A tester or reviewer run that moved its PR to changes-requested: count it against that check (rule 5).
  # They are only woken on needs-test or needs-review PRs, so the label is this run's doing.
  if [[ $agent =~ ^(tester|reviewer)$ ]] && [[ $key =~ ^pr-[0-9]+$ ]] &&
     [ "$(gh pr view "${key#pr-}" -R "$REPO" --json labels --jq 'any(.labels[]; .name == "changes-requested")' 2>/dev/null)" = true ]; then
    sent_back "${key#pr-}" "$([ "$agent" = tester ] && echo test || echo review)"
  fi
  # A failed run that ended on a rate limit, after the runtime tried every model in the agent's chain.
  if [ "$rc" -ne 0 ] && [ -n "$(rate_limit_error "$agent" "$out")" ]; then
    rc=5
    log "rate-limited: $agent on $key, on all its models"
  fi
  log "done $agent $key exit=$rc secs=$secs $(jq -r '"model=\(.model // "?") in=\(.input // "?") out=\(.output // "?")"' <<<"$usage")"
  jq -c --arg t "$(now)" --arg a "$agent" --arg k "$key" --arg r "${RUNTIME[$agent]}" --argjson rc "$rc" --argjson s "$secs" \
    --arg task "$task" '{time: $t, agent: $a, runtime: $r, key: $k, exit: $rc, secs: $s} + . + {task: $task}' \
    <<<"$usage" >> "$STATE/runs.jsonl"

  # Rate-limited: not the item's fault, so no failure is counted and it keeps its labels. The agent cools down.
  # Any other run in which a model wrote something ends the agent's cooldown.
  if [ "$rc" -eq 5 ]; then
    cool_down "$agent" "$key" "$out"
    return 0
  fi
  if [ "$(jq -r '.output // 0' <<<"$usage")" -gt 0 ]; then
    recovered "$agent" "$(jq -r --arg m "$model" '.model // $m' <<<"$usage")"
  fi

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

# new_lessons: Lesson: lines posted since the last merged lessons PR was opened, minus those already in LESSONS.md,
# newest 10. Every agent gets them on its next wake-up, before the orchestrator curates them and the human merges.
# (Nothing else runs while the orchestrator writes a lessons PR, so no lesson falls between it and the merge.)
new_lessons() {
  local since
  since=$(gh pr list -R "$REPO" --state merged --label lessons --limit 1 --json createdAt --jq '.[0].createdAt // empty' 2>/dev/null) || true
  [ -n "$since" ] || since=$(date -u -d '7 days ago' +%FT%TZ)
  gh api "repos/$REPO/issues/comments?since=$since&per_page=100" --paginate \
    --jq '.[] | select(.user.login == "'"$BOT"'") | .html_url as $u | .body | scan("(?m)^Lesson:.*$") | "- \(.) (\($u))"' 2>/dev/null \
    | grep -v -F -f <(grep -oE 'https://github\.com/[^ )]+' "$ROOT/agents/LESSONS.md") | tail -10 || true
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

# run_usage <agent> <output file>: {provider, model, input, cached, output, fallbacks, tools} as JSON, {} if unreadable.
# input excludes cached tokens (prompt re-read from the provider's cache), as both runtimes report it.
# tools = number of tool calls that ran (OpenClaw: distinct tools used; Hermes: tool calls).
# Hermes reports only the requested model; a fallback shows up in its text output, not here.
run_usage() {
  case ${RUNTIME[$1]} in
    openclaw) sed -n '/^{/,$p' "$2" | jq -c '.meta.agentMeta // {} | {provider, model,
                input: .usage.input, cached: .usage.cacheRead, output: .usage.output,
                fallbacks: (.fallbackAttempts // [] | length),
                tools: (.terminalReceipt.successfulToolNames // [] | map(select(. != "tool_search")) | length)}' ;;
    hermes) jq -cRn '[inputs | fromjson? // empty] as $l
              | ($l | map(select(.type == "system")) | first // {}) as $init
              | ($l | map(select(.type == "result")) | last // {}) as $r
              | {model: $init.model, input: $r.tokens.input, cached: $r.tokens.cache_read, output: $r.tokens.output,
                 tools: ($l | map(select(.type == "tool_use")) | length)}' "$2" ;;
  esac 2>/dev/null || echo '{}'
}

# ---------------------------------------------------------------- rate limits

# A rate-limited agent is skipped for 15 min, then 30, 60, at most 120, while the other agents keep working.
# The first time, the dispatcher files one rate-limit issue for the human. It closes when the agent runs again.
mkdir -p "$STATE/cooldown"                     # cooldown/<agent>: "<until, epoch s> <times in a row> <issue>"
RATE_LIMIT='\b429\b|too many requests|rate.?limit|resource.?exhausted|quota|usage limit|limit (reached|exhausted|exceeded)|throttl'
declare -A SKIPPED=()

# rate_limit_error <agent> <output file>: the error the run ended on, if it is a rate limit; nothing otherwise.
# Only the runtime's own error is read, never tool output: the app has a rate limit, and its code and tests say so.
rate_limit_error() {
  local err=${2%.out}.err line=""
  case ${RUNTIME[$1]} in
    openclaw)  # one log line per model attempt; the last one is how the run ended
      line=$(grep -hE 'All models failed|embedded run agent end:' "$err" 2>/dev/null | tail -1)
      grep -qE 'isError=true|All models failed' <<<"$line" || line="" ;;
    hermes)    # the result record's error; stderr only when the run died before writing one
      if grep -q '"type": *"result"' "$2" 2>/dev/null; then
        line=$(jq -rR 'fromjson? | select(.type == "result" and .exit_code != 0) | .error // empty' "$2" 2>/dev/null | tail -1)
      else
        line=$(grep -iE "$RATE_LIMIT" "$err" 2>/dev/null | tail -1)
      fi ;;
  esac
  grep -iqE "$RATE_LIMIT" <<<"$line" || return 0
  sed -E 's/[A-Za-z0-9_-]{32,}/…/g' <<<"$line" | cut -c1-300   # no long IDs or anything key-like in the issue
}

# cooling <agent>: true while the agent is cooling down. Logs the skip once per dispatcher run.
cooling() {
  local until
  read -r until _ 2>/dev/null < "$STATE/cooldown/$1" || return 1
  [ "$(date +%s)" -lt "$until" ] || return 1
  [ -n "${SKIPPED[$1]:-}" ] || log "skip $1: rate-limited, next try $(date -u -d "@$until" +%H:%MZ)"
  SKIPPED[$1]=1
}

# cool_down <agent> <key> <output file>: after a rate-limited run. Files the rate-limit issue, or comments on it.
cool_down() {
  local agent=$1 key=$2 out=$3 f=$STATE/cooldown/$1 times=0 issue="" mins until body
  read -r _ times issue 2>/dev/null < "$f" || true
  times=$(( ${times:-0} + 1 ))
  mins=$(( times >= 4 ? 120 : 15 << (times - 1) ))   # 15, 30, 60, then 120
  until=$(( $(date +%s) + mins * 60 ))
  body=$STATE/runs/$(date -u +%Y%m%dT%H%M%SZ)-$agent-rate-limit.md
  if [ -z "$issue" ]; then
    cat > "$body" <<EOF
**$agent can't reach any of its models: they are all rate-limited.**

- **When:** $(now), working on $key. That item keeps its labels and waits; no failure is counted.
- **Models:** its main model and both backups (WORKFLOW.md, Team). The runtime moves down that list before it gives up.
- **Last error:** \`$(rate_limit_error "$agent" "$out")\`

**What the dispatcher does:** it skips $agent until $(date -u -d "@$until" +%H:%M) UTC, then tries again,
waiting 15, 30, 60, then 120 minutes between tries. The other agents keep working. This issue closes by itself
when $agent runs again.

**What you can do:** wait, check the provider's usage page, or give $agent another model
(\`agents/openclaw/openclaw.json5\` or \`agents/hermes/$agent.json\`, then \`agents/setup.sh\`).

— dispatcher · no model
EOF
    issue=$(gh issue create -R "$REPO" --title "Rate limit: $agent can't reach any of its models" \
      --label rate-limit --label needs-human --body-file "$body" 2>/dev/null) || true
    issue=${issue##*/}
  else
    printf '%s\n\n— dispatcher · no model\n' "Still rate-limited at $(now), on $key: \`$(rate_limit_error "$agent" "$out")\`. \
Next try at $(date -u -d "@$until" +%H:%M) UTC." > "$body"
    gh issue comment "$issue" -R "$REPO" --body-file "$body" >/dev/null 2>&1 || true
  fi
  echo "$until $times $issue" > "$f"
  log "cooldown $agent: $mins min (rate-limited $times time(s) in a row), issue #${issue:-?}"
}

# recovered <agent> <model>: a model answered again. Ends the cooldown and closes the rate-limit issue.
recovered() {
  local f=$STATE/cooldown/$1 issue=""
  [ -e "$f" ] || return 0
  read -r _ _ issue < "$f" || true
  rm -f "$f"
  if [ -n "$issue" ]; then
    gh issue close "$issue" -R "$REPO" --comment "Recovered at $(now): $1 ran again, on $2.

— dispatcher · no model" >/dev/null 2>&1 || true
  fi
  log "recovered $1, closed #${issue:-?}"
}

# ---------------------------------------------------------------- 0. released by the human

# needs-human's exit: the human removes the label, after a comment, a ticket edit or a decision. The dispatcher sees it
# go (it keeps the list of held items in held) and gives the item a fresh start: its send-back and failed-run counts are
# reset. A PR re-enters where its pipeline label says. The human may swap that label first: changes-requested (default:
# the builder tries again, reading the human's comment), needs-test or needs-review (test or review it again).
# A PR left with no pipeline label goes back to its builder.
held_now=$(jq -rn --argjson p "$prs" --argjson i "$issues" '
  ($p[] | select(any(.labels[]; .name == "needs-human")) | "pr-\(.number)"),
  ($i[] | select(any(.labels[]; .name == "needs-human")) | "issue-\(.number)")')
released=0 rlabel=""
if [ -e "$STATE/held" ]; then
  for key in $(comm -23 <(sort -u "$STATE/held") <(sort -u <<<"$held_now")); do
    n=${key#*-}
    case $key in                                 # closed or merged while held: nothing to release
      pr-*) jq -e --argjson n "$n" 'any(.[]; .number == $n)' <<<"$prs" >/dev/null || continue ;;
      issue-*) jq -e --argjson n "$n" 'any(.[]; .number == $n)' <<<"$issues" >/dev/null || continue ;;
    esac
    if [ "$DRY_RUN" != 1 ]; then
      rm -f "$STATE"/fails/*-"$key"
      [ "$key" = "pr-$n" ] && [ -e "$SENDBACKS/$key" ] && mv "$SENDBACKS/$key" "$SENDBACKS/$key.before-$(date -u +%Y%m%dT%H%M%SZ)"
    fi
    if [ "$key" = "pr-$n" ]; then
      rlabel=$(jq -r --argjson n "$n" '.[] | select(.number == $n) | [.labels[].name
        | select(IN("needs-test", "needs-review", "changes-requested", "ready-to-merge"))] | first // ""' <<<"$prs")
      if [ -z "$rlabel" ]; then
        act gh pr edit "$n" -R "$REPO" --add-label changes-requested >/dev/null
        rlabel=changes-requested
      fi
      comment "$n" "Dispatcher: released by the human. Send-back and failed-run counts are reset, and the PR re-enters at \`$rlabel\`."
    fi
    log "released $key by the human: counts reset${rlabel:+, re-enters at $rlabel}"
    rlabel=""
    released=1
  done
fi
[ "$DRY_RUN" = 1 ] || printf '%s\n' "$held_now" > "$STATE/held"
[ "$released" = 0 ] || load_prs

# ---------------------------------------------------------------- 0a. tickets the human didn't ask for

# Work must trace back to the human, so agents can't create work for each other without end.
# Work is a ticket the human (the repo owner) filed, or a split of one: a ticket by the agents' account with a line
# "Part of #N" and the orchestrator's signature, where the human filed #N. Anything else gets needs-approval and waits:
# a tester's bug, tech-debt, a split of a split, an issue from a stranger on the public repo. The human removes the
# label to let the team work on it, or closes the issue. Each issue is checked once (gated/<n>), so a removed label
# stays removed.
mkdir -p "$STATE/gated"
owner_issues=$(gh issue list -R "$REPO" --state all --author "$OWNER" --limit 300 --json number --jq '[.[].number]')
gated=0
while read -r n author parent; do
  [ -n "$n" ] && [ ! -e "$STATE/gated/$n" ] || continue
  if [ "$author" = "$OWNER" ] ||
     { [ "$author" = "$BOT" ] && [ -n "$parent" ] && jq -e --argjson p "$parent" 'index($p)' <<<"$owner_issues" >/dev/null; }; then
    [ "$DRY_RUN" = 1 ] || echo work > "$STATE/gated/$n"
    continue
  fi
  act gh issue edit "$n" -R "$REPO" --add-label needs-approval >/dev/null
  act gh issue comment "$n" -R "$REPO" --body "Dispatcher: this ticket wasn't filed by the human or split from one of their tickets, so it waits for the human's OK. To let the team work on it, remove \`needs-approval\`. Otherwise close it.

— dispatcher · no model" >/dev/null
  [ "$DRY_RUN" = 1 ] || echo needs-approval > "$STATE/gated/$n"
  log "needs-approval #$n (filed by $author${parent:+, part of #$parent})"
  gated=1
done < <(jq -r '.[] | select(all(.labels[]; .name != "rate-limit" and .name != "needs-approval"))
  | (.body // "") as $b | [.number, .author.login,
      (if $b | test("— orchestrator · ") then ($b | capture("(?im)^[^a-z0-9#]*part of[^#\\n]*#(?<p>[0-9]+)").p // "") else "" end)]
  | @tsv' <<<"$issues")
[ "$gated" = 0 ] || load_issues                 # an agent may have assigned it in the same run it filed it

# ---------------------------------------------------------------- 0b. PRs that conflict with main, or fell behind it

# Builders work at the same time on branches cut from different points of main. Plain git rules, no model:
# - A PR that conflicts with main goes back to its owner, to merge origin/main in and resolve it.
#   It's not the PR's fault, so it doesn't count against its send-backs.
# - main only takes a PR that is up to date with it (the ruleset's strict check), so nothing merges untested on top of
#   the latest main. A PR about to be tested, or waiting for the human, that fell behind gets main merged in by GitHub.
#   The new commit has no test or review yet, so the PR goes round again.
# lessons PRs are the human's to handle.
changed=0
while read -r n branch sha mergeable label; do
  [ -n "$n" ] || continue
  owner=${branch%%/*}
  next=needs-test; [ "$owner" = tester ] && next=needs-review   # nobody tests the tester's own PR
  if [ "$mergeable" = CONFLICTING ]; then
    [ "$label" = changes-requested ] && continue                 # already with its owner
    relabel "$n" changes-requested "$label"
    comment "$n" "Dispatcher: this PR conflicts with \`main\`. Merge \`origin/main\` into the branch, resolve the conflicts, run the tests, push. (This doesn't count as a send-back.)"
    log "conflict pr-$n: back to $owner"
    changed=1
  elif [[ $label =~ ^(needs-test|ready-to-merge)$ ]] &&
       [ "$(gh api "repos/$REPO/compare/main...$sha" --jq .behind_by 2>/dev/null || echo 0)" -gt 0 ]; then
    act gh api -X PUT "repos/$REPO/pulls/$n/update-branch" -f expected_head_sha="$sha" >/dev/null || continue
    [ "$label" = "$next" ] || relabel "$n" "$next" "$label"
    comment "$n" "Dispatcher: \`main\` moved on, so main was merged into this branch. The new commit is tested and reviewed again before it can merge."
    log "behind pr-$n: merged main in, back to $next"
    changed=1
  fi
done < <(jq -r '.[] | select(all(.labels[]; .name != "needs-human" and .name != "lessons"))
  | ([.labels[].name] | map(select(IN("needs-test", "needs-review", "changes-requested", "ready-to-merge"))) | first // "") as $l
  | select($l != "") | "\(.number) \(.headRefName) \(.headRefOid) \(.mergeable) \($l)"' <<<"$prs")
[ "$changed" = 0 ] || load_prs

# ---------------------------------------------------------------- 1. reviewer

while read -r n branch sha; do
  [ -n "$n" ] || continue
  cooling reviewer && break
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
    success) cooling tester && continue
             wake tester "pr-$n" "PR #$n needs testing."; exit 0 ;;
    failure)
      rerun_ci "$n" && continue                  # a flake gets one more try; wait for the re-run
      relabel "$n" changes-requested needs-test
      comment "$n" "Dispatcher: CI (pytest) failed on this PR, so it goes back to the builder without testing. (A failure is re-run once per commit first, in case it was a flake.) See the checks tab."
      sent_back "$n" ci
      load_prs ;;
    pending) ;;
  esac
done < <(prs_with needs-test)

# ---------------------------------------------------------------- 3. builders: sent-back PRs, then new issues

while read -r n branch sha; do
  [ -n "$n" ] || continue
  owner=${branch%%/*}
  [[ " ${BUILDERS[*]} " == *" $owner "* ]] || { log "skip pr-$n: branch $branch has no builder prefix"; continue; }
  over=$(over_budget "$n")
  if [ -n "$over" ]; then
    act gh pr edit "$n" -R "$REPO" --add-label needs-human >/dev/null   # keeps changes-requested: where it re-enters
    comment "$n" "Dispatcher: sent back a third time by the same check ($over). Over to the human (WORKFLOW.md rule 5). Send-backs so far: $(sendback_counts "$n")."
    log "needs-human pr-$n: third send-back by $over ($(sendback_counts "$n"))"
    continue
  fi
  cooling "$owner" && continue
  wake "$owner" "pr-$n" "PR #$n was sent back. Fix it."
  exit 0
done < <(prs_with changes-requested)

for agent in "${BUILDERS[@]}"; do
  for n in $(issues_with "assign:$agent"); do
    has_open_pr "$agent" "$n" && continue
    cooling "$agent" && break
    wake "$agent" "issue-$n" "Issue #$n is assigned to you."
    exit 0
  done
done

# ---------------------------------------------------------------- 4. orchestrator: daily lessons

# Once a day, collect the Lesson: lines (at the start of a line) posted since the last collection. The orchestrator
# folds them into agents/LESSONS.md in a PR labelled lessons, which the human merges. Skipped while one is open.
today=$(date -u +%F)
if [ "$(cat "$STATE/lessons-day" 2>/dev/null)" != "$today" ] && [ -z "$(prs_with lessons)" ] && ! cooling orchestrator; then
  since=$(cat "$STATE/lessons-since" 2>/dev/null || date -u -d '1 day ago' +%FT%TZ)
  lessons=$(gh api "repos/$REPO/issues/comments?since=$since&per_page=100" --paginate \
    --jq '.[] | select(.user.login == "'"$BOT"'") | .html_url as $u | .body | scan("(?m)^Lesson:.*$") | "- \(.) (\($u))"')
  if [ "$DRY_RUN" != 1 ]; then echo "$today" > "$STATE/lessons-day"; now > "$STATE/lessons-since"; fi
  if [ -n "$lessons" ]; then
    wake orchestrator lessons "Collect lessons. These Lesson: lines were posted since $since:
$lessons"
    exit 0
  fi
fi

# ---------------------------------------------------------------- 5. orchestrator: the board

# The board leaves out rate-limit issues (the dispatcher's, for the human) and issues waiting for the human's OK.
# When the human removes needs-approval, the issue shows up as new, and the orchestrator triages it.
board() {
  gh issue list -R "$REPO" --state all --limit 300 --json number,state,labels \
    --jq 'map(select(all(.labels[]; .name != "rate-limit" and .name != "needs-approval")))
      | map({n: .number, s: .state, l: ([.labels[].name] | sort)}) | sort_by(.n)'
}
save_board() { [ "$DRY_RUN" = 1 ] || board > "$STATE/board.json"; }

current=$(board)

# A token report on every closed ticket, once: its runs and its PRs' runs, from runs.jsonl (ops/token_report.py, no model).
mkdir -p "$STATE/reported"
for n in $(jq -r '.[] | select(.s == "CLOSED") | .n' <<<"$current"); do
  [ -e "$STATE/reported/$n" ] && continue
  if [ "$DRY_RUN" = 1 ]; then echo "would: post the token report on #$n"; continue; fi
  if python3 "$ROOT/ops/token_report.py" issue "$n" > "$STATE/reported/$n.md" &&
     gh issue comment "$n" -R "$REPO" --body-file "$STATE/reported/$n.md" >/dev/null; then
    touch "$STATE/reported/$n"
    log "token report posted on #$n"
  fi
done

# An assign:orchestrator issue wakes it once. If the label is still there afterwards, only a board change wakes it again.
for n in $(issues_with assign:orchestrator); do
  if [ -e "$STATE/handled-issue-$n" ] && [ "$current" = "$(cat "$STATE/board.json" 2>/dev/null)" ]; then
    continue
  fi
  cooling orchestrator && break
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
if [ -n "$changes" ] && ! cooling orchestrator; then   # while it cools, changes pile up for its next run
  wake orchestrator board "The board changed since your last run: $changes."
  save_board
fi
