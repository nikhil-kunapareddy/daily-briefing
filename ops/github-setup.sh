#!/usr/bin/env bash
# One-time GitHub setup: labels and the rule that protects main. Safe to re-run.
# Run from the laptop as the repo owner (admin). The agents' machine account can't do this.
#   ops/github-setup.sh
set -euo pipefail

REPO=nikhil-kunapareddy/daily-briefing

# ---------------------------------------------------------------- labels (agents/WORKFLOW.md)

label() { gh label create "$1" -R "$REPO" --color "$2" --description "$3" --force >/dev/null && echo "  $1"; }
echo "== labels"
for a in orchestrator builder-ingest builder-web builder-ops tester; do
  label "assign:$a" 1d76db "Owned by $a. Set by the orchestrator only"
done
label needs-test        fbca04 "PR waits for the tester"
label needs-review      0e8a16 "PR passed testing; waits for the reviewer"
label changes-requested d93f0b "PR sent back to its builder"
label ready-to-merge    0e8a16 "PR approved; waits for the human"
label needs-human       b60205 "Stuck or sent back too often; waits for the human"
label lessons           c5def5 "Update to agents/LESSONS.md; the human reviews it"
label bug               d73a4a "Something is broken"
label incident          b60205 "The live app or its schedule is failing"
label tech-debt         cfd3d7 "Worth fixing later; not a bug"
label change-request    a2eeef "A change asked for by the human"
label rate-limit        b60205 "An agent can't reach any of its models. Filed and closed by the dispatcher"

# ---------------------------------------------------------------- rule for main
# Every change arrives by PR. A PR merges only with green CI, the tester's and reviewer's statuses, and an
# approval from someone other than its author. All PRs come from the agents' account, so only the human can
# approve and merge. The repo admin (the human) can bypass, for setup commits and lessons PRs.

echo "== rule for main"
body=$(cat <<'EOF'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [ { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" } ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          { "context": "pytest" },
          { "context": "test/passed" },
          { "context": "review/approved" } ] } }
  ]
}
EOF
)
id=$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name == "main") | .id')
if [ -n "$id" ]; then
  gh api -X PUT "repos/$REPO/rulesets/$id" --input - <<<"$body" >/dev/null && echo "  updated ruleset $id"
else
  gh api -X POST "repos/$REPO/rulesets" --input - <<<"$body" --jq '"  created ruleset \(.id)"'
fi
echo "== done"
