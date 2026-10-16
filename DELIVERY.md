# Delivery

**Live:** https://dailyresearch.duckdns.org · **Repo:** https://github.com/nikhil-kunapareddy/daily-briefing

The product: the 5 newest arXiv **cs.MA** papers, what is new since the last run, a run history, and a rate-limited
**Run now** button. The team: six agents, four on OpenClaw and two on Hermes Agent, handing work along through
GitHub issues and PRs. A plain-code dispatcher wakes the agent each step needs. The human files tickets and merges.

Timeline (UTC): a dry run of the whole team on Oct 14 (issue #1, PRs #4–#6). The real build started Oct 16 at 00:01
with one ticket, "Plan the build from `SPEC.md`" (#8). The site was up at 03:10. A bug filed by the human (#29) went
through the full loop, and Run now saved its first 5 real papers at 06:05.

Numbers below are as of Oct 16, about 07:00 UTC.

## 1. The agent roster

| Agent | Runtime | Role | Main model (backups) | Tools |
|---|---|---|---|---|
| orchestrator | Hermes | Splits tickets, assigns them, re-queues work | `nemotron-3-ultra` (`nemotron-3-ultra:free`, `gemini-2.5-flash`) | 7: `terminal`, `process_manage`, `read_file`, `write_file`, `patch`, `search_files`, `todo_list` |
| builder-ingest | OpenClaw | arXiv request, the run, the database | `gpt-oss:120b` (`nemotron-3-ultra`, `gemini-2.5-flash`) | 9: `read`, `write`, `edit`, `apply_patch`, `exec`, `process`, `tool_search`, `tool_describe`, `tool_call` |
| builder-web | OpenClaw | The page, Run now, `/healthz` | `gpt-oss:120b` (same as above) | same 9 |
| builder-ops | OpenClaw | `scripts/install.sh`: the app as a service on the VM, and its cron line | `gpt-oss:120b` (same as above) | same 9 |
| tester | OpenClaw | Runs each PR on test data; passes it or sends it back with evidence; files bugs | `gemma4:31b` (`nemotron-3-ultra`, `gemini-3.5-flash-lite`, 2 OpenRouter free models) | same 9 |
| reviewer | Hermes | Reads each tested PR against its ticket and `SPEC.md`; approves or sends it back | `gemini-2.5-flash` (`gemini-3.5-flash-lite`, `gemma4:31b`) | same 7 as the orchestrator |

Limits on the tools: OpenClaw's `exec` runs only 45 allowlisted programs (git, gh, python, curl, sqlite3, …) and its
file tools stay in the agent's own folder. Hermes's `terminal` has no allowlist, only its "smart" approvals. No agent
has sudo, web search, a browser or access to the live data. All agents share one GitHub bot account with write
access; it can't push to `main` or merge. Each agent commits under its own name.

**Why this team.** It is the brief's suggested team plus the optional ops builder. `scripts/install.sh` (a user
systemd service, no sudo, safe to re-run) was the riskiest ticket for "it runs", and it was crowding builder-ingest's
lane (`DECISIONS.md` #17). Models by role, all free: the strongest free model plans (`nemotron-3-ultra`, 550B), a
cheaper one builds (`gpt-oss:120b`), and the two checks use other model families than the builders (OpenAI): the
tester `gemma4:31b`, the reviewer `gemini-2.5-flash` (both Google). The VM is on AWS (the brief's plan B): Oracle had
no capacity after 42 tries (`DECISIONS.md` #6, #7).

**Who wrote what.** The agents wrote `app/`, `tests/`, `fixtures/` and `scripts/install.sh`. The human, with
Claude Code, wrote `SPEC.md`, `infra/`, `agents/`, `ops/` and the docs, and made the live fixes marked as human
changes in `DECISIONS.md` (#42, #43).

## 2. How a ticket moves

```mermaid
flowchart TD
  I["Ticket: a GitHub issue<br/>filed by the human (plan, bug, change request)"]
  O["orchestrator: triage<br/>split into one-PR tickets · assign a lane"]
  B["builder: branch from main<br/>PR says Refs #N · label needs-test"]
  C{{"CI: pytest"}}
  T{{"tester: run it on test data"}}
  R{{"reviewer: PR vs ticket and SPEC.md"}}
  S{{"dispatcher: in step with main?"}}
  M["human: approve and merge"]
  W{{"dispatcher: works in main?<br/>CI on main · deployed · /healthz 200"}}
  D(["ticket closed"])
  H(["needs-human: waits for the human"])

  I --> O --> B --> C
  C -->|green| T
  T -->|test/passed| R
  R -->|review/approved| S
  S -->|ready-to-merge| M --> W
  W -->|yes| D
  C -.->|red: re-run once, then sent back| B
  T -.->|sent back with evidence and a Lesson| B
  R -.->|sent back with a reason| B
  S -.->|conflict: back to builder · behind: main merged in, test again| B
  W -.->|broken in main: fix in a new PR| B
  T -.->|"3rd send-back by one check"| H
```

The dispatcher (`ops/dispatcher.sh`, bash, every 2 minutes and the moment a run ends) moves each ticket by waking the
one agent the next step needs. It is plain code, no model. It checks that work traces back to a human ticket,
retries a failed run once on the agent's backup model, pauses only the agent whose models are all rate-limited, and
closes a ticket only once its code works in main. The full diagram, with every rule: `agents/WORKFLOW.md`.

The tickets of this build:
- #8 "Plan the build from SPEC.md" → #9 config and database · #10 fixture · #11 install script · #12 arXiv and job ·
  #13 page · #14 Run now and `/healthz` · #15 behaviour tests (all `Part of #8`).
- #29 bug "Run now records a run but never fetches papers" → #30 (builder-ingest) · #31 (builder-web).

## 3. What each agent did

| Agent | Runs | What it did |
|---|---:|---|
| orchestrator | 18 | Split #8 into 7 tickets in one 6-minute run, with dependencies (#11–#14 needed #9). Assigned each wave as tickets closed. Split bug #29 into #30 and #31. Mistakes: closed #29 before #31 was done (the human reopened it), and #30's text lost its code names |
| builder-ingest | 11 | PR #16 (config, schema, db functions), PR #19 (arXiv fetch, parse, one run), PR #33 (`job.run(run_id)` for Run now). Fixed the tester's bug in PR #19 in 69 s. Mistakes: PR #19 replaced the tester's real arXiv fixture with made-up entries; PR #33 reused #12's branch name |
| builder-web | 14 | PR #20 (the page, Run now, `/healthz`), PR #36 (Run now calls the job). Mistakes: four PRs that claimed a fix they didn't make (#22: trailing spaces only; #23: a `try/except` that couldn't fix it; #25: one re-indented line; #26: only scratch files). The human closed all four |
| builder-ops | 4 | PR #21 (`install.sh`), then PR #24, which fixed the systemd service after the works-in-main check sent #11 back (502) and the human posted the service's error |
| tester | 24 | Tested every builder PR. PR #17 (a real arXiv response as the fixture), PR #28 (behaviour tests: 5 new then 0 new, the version rule, failed fetches, the rate limit, `/healthz`). Sent PR #19 back with a real bug: a non-breaking hyphen in the User-Agent made every arXiv request crash |
| reviewer | 19 | Reviewed every tested PR and approved every one, including the out-of-lane fixture rewrite and the no-op PRs. It never sent a PR back in the build |

The dispatcher, in the same period: 8 PRs updated after falling behind main, 4 conflicts sent back to their
builder, 7 merged PRs passed by the works-in-main check (one credited to the wrong ticket, see §5) and 2 sent back
by it, 6 runs caught with no progress or no tool
calls and retried on a backup model, and 8 rate-limit pauses, each with a ticket for the human.

On `main`: 17 commits authored by the agents (builder-ingest 6, builder-web 5, builder-ops 3, tester 2,
orchestrator 1), all 17 PRs opened by the agents' account, 12 merged by the human and 5 closed unmerged.

## 4. One thing that broke: Run now never fetched anything

- **What:** on the live page, Run now added a row marked ✓ "no new submissions", and no paper ever appeared.
  The database showed three runs with no finish time and no papers.
- **How it was caught:** by the human, looking at the live page after the build. Not by the team: the Run now test
  only checked the rate limit, the flow tests called the job directly, and the reviewer approved PR #20.
- **Why:** the two builders' code didn't connect. `SPEC.md` says Run now runs the job inside the request,
  `app.job.run(run_id)`. builder-ingest wrote `job.run(trigger)`, which makes its own row, and builder-web's
  `run_now()` never called it. A run with no error was shown as ✓ even though it never finished.
- **The fix, through the team:** the human filed bug #29 with the cause and acceptance criteria. The orchestrator
  split it: #30 for builder-ingest (PR #33: `job.run()` takes a `run_id`, unfinished runs aren't ✓) and #31 for
  builder-web (PR #36: Run now calls the job). Both were tested, reviewed and passed the works-in-main check.
  At 06:05 UTC Run now saved 5 papers in 3 seconds.

Other things that broke, and how each was caught:

| What broke | Caught by | Fixed by |
|---|---|---|
| `gemini-2.5-flash` allows 20 requests a day; the tester's retries all hit 429 and it was paused 55 min (issue #18) | the dispatcher's rate-limit pause and ticket | the human: a new tester model list (`DECISIONS.md` #42) |
| Ollama Cloud's free usage ran out after one night, about 1,800 requests; every Ollama model failed (issue #32) | the same pause and ticket | the human: a second Ollama key, tried after the first (`DECISIONS.md` #43) |
| `install.sh` wrote a service that couldn't start (`$HOME` in `ExecStart`, the wrong entry point, no data folder setting), and reported success | the works-in-main check: `/healthz` answered 502 | builder-ops, PR #24, after the human posted the service's error, which the agents can't read |
| The reviewer approved an out-of-lane fixture rewrite (merged) and four no-op PRs | the human | the human closed the no-op PRs and asked the tester for a real-fixture test |
| Models ended runs with the answer only in their reply, or with no tool call | the dispatcher's no-progress check | a retry on the backup model |
| The schedule's cron line can't start the job (`python` not found, wrong folder, no data folder setting) | the human, before the first scheduled run | open at the time of writing |

## 5. What I would change

1. **Check scope in plain code, before the reviewer.** The dispatcher would send back any PR that touches files
   outside its author's lane (the code layout in `SPEC.md`) or changes only whitespace. The reviewer missed both.
2. **A stronger reviewer with a checklist.** It approved every PR it reviewed. The brief suggests a stronger model for
   review; I chose `gemini-2.5-flash` to keep it out of the builders' and orchestrator's model families, and it
   became the weakest gate.
3. **Test what the user does, end to end.** The works-in-main check should also press Run now and see papers saved,
   and run the cron line as the app's user. And builder-ops needs a read-only way to see its service's logs.
4. **Budget for capacity.** The free tiers ran out in one night. Pay for one plan (Ollama Pro is $20/month), rest
   a single model instead of the whole agent, and read only a run's final error when deciding it was rate-limited:
   a first key's handled 429 still paused agents.
5. **Start every ticket from a clean `main`, and enforce branch names.** The tester twice built on another PR's
   branch and carried its files along; builder-ingest reused another ticket's branch name, so the works-in-main
   check credited the wrong ticket.

## 6. Token and model report

From the dispatcher's run log (`ops/token_report.py all`): each runtime's own token counts per run, no estimates.
Two caveats: Hermes logs the model asked for, so a reviewer run that fell back to `gemini-3.5-flash-lite` inside the
run is counted as `gemini-2.5-flash`; and the totals include the Oct 14 dry run (#2, #3).

**Where the tokens went, in plain words:**
- **90 runs, 49.2M tokens in all.** 99% is prompt the model reads: instructions, files and command output, re-sent
  on every turn. Only 1% is text the models write. 89% of the prompt came from the provider's cache.
- **Half of the new input went to retries:** failed runs, fixes after a send-back, and re-tests after a PR fell
  behind `main`. One re-test of an empty PR (#16, after two pushes to `main`, one of them the human's) took 485K tokens.
- **The orchestrator is the biggest single spender (28% of new input).** It re-reads the board on every change; one
  label change cost a 466-second run and 165K tokens.
- **Builds are cheap, fixes are not:** the first build of every ticket took 215K input tokens in all; the builders'
  fixes and retries took 1.8M.

| Phase | Runs | Input | Cached | Output | Time |
|---|---:|---:|---:|---:|---:|
| planning (orchestrator) | 18 | 1,526,770 | 1,848,160 | 65,275 | 45 min |
| coding (first build of a ticket) | 7 | 215,146 | 4,345,648 | 64,581 | 12 min |
| review/test (first test and review of a PR) | 25 | 1,007,403 | 8,779,413 | 46,435 | 26 min |
| retries (failed runs, fixes, repeats) | 40 | 2,767,488 | 28,312,413 | 260,819 | 99 min |

| Agent | Runs | Input | Cached | Output |
|---|---:|---:|---:|---:|
| orchestrator | 18 | 1,526,770 | 1,848,160 | 65,275 |
| reviewer | 19 | 1,058,347 | 2,456,137 | 18,380 |
| builder-web | 14 | 1,031,429 | 16,025,568 | 144,477 |
| tester | 24 | 997,221 | 13,056,233 | 98,065 |
| builder-ingest | 11 | 819,803 | 7,815,856 | 85,527 |
| builder-ops | 4 | 83,237 | 2,083,680 | 25,386 |

| Model | Runs | Input | Cached | Output |
|---|---:|---:|---:|---:|
| `nemotron-3-ultra` (Ollama) | 23 | 3,264,407 | 8,374,048 | 125,756 |
| `gemini-2.5-flash` (Gemini API) | 20 | 1,047,668 | 2,194,224 | 18,438 |
| `gpt-oss:120b` (Ollama) | 23 | 682,057 | 20,389,904 | 206,982 |
| `gemma4:31b` (Ollama) | 20 | 442,600 | 12,024,800 | 85,103 |
| `gemini-3.5-flash-lite` (Gemini API) | 1 | 80,075 | 302,658 | 831 |

Input excludes cached tokens. Per-ticket numbers: `python3 ops/token_report.py issue N` on the VM, also posted as a
comment on each ticket when it closes. Every model call is also traced in Langfuse.

## 7. Trace excerpts

Real lines from the dispatcher's log on the VM (`~agents/work/.dispatcher/dispatch.log`), long lines cut.

**A handoff:** the works-in-main check sends #11 back; builder-ops fixes it; tester, reviewer, human merge; closed.
```
02:54:05Z broken in main: pr-21 (b7cd539…): https://dailyresearch.duckdns.org/healthz answered 502, expected 200, after deploying b7cd539; #11 back to its builder
02:56:11Z wake builder-ops issue-11: Issue #11 is assigned to you. Its PR merged, but it doesn't work in main: …/healthz answered 502 …
02:58:22Z done builder-ops issue-11 exit=0 secs=129 model=gpt-oss:120b in=26984 out=7356
03:06:19Z wake tester pr-24: PR #24 needs testing.
03:07:35Z done tester pr-24 exit=0 secs=75 model=gemma4:31b in=22290 out=1917
03:07:37Z wake reviewer pr-24: PR #24 needs review.
03:08:25Z done reviewer pr-24 exit=0 secs=47 model=gemini-2.5-flash in=58238 out=643
03:10:37Z works in main: pr-24 (315622a…), closed #11
```

**A rejection:** the tester sends PR #19 back; builder-ingest fixes it; it passes.
```
01:53:42Z wake tester pr-19: PR #19 needs testing.
01:55:07Z send-back pr-19 by test: 1 so far, 2 allowed
01:55:10Z lessons: added 1 to #7
01:55:12Z wake builder-ingest pr-19: PR #19 was sent back. Fix it.
01:56:22Z done builder-ingest pr-19 exit=0 secs=69 model=gpt-oss:120b in=16051 out=3703
02:12:17Z done tester pr-19 exit=0 secs=63 model=gemma4:31b in=16029 out=1544
02:12:42Z done reviewer pr-19 exit=0 secs=21 model=gemini-2.5-flash in=52603 out=438
```
The tester's comment on PR #19:
> The PR fails behaviorally because the `USER_AGENT` in `app/config.py` contains a non-breaking hyphen (`‑`)
> instead of a standard hyphen. This causes the `requests` library to raise a `UnicodeEncodeError` …
> Lesson: Use standard ASCII characters in HTTP headers to avoid encoding errors.
> — tester · gemma4:31b

**A retry:** the reviewer ends its run without posting anything; the dispatcher retries it on the backup model.
```
00:28:31Z wake reviewer pr-16: PR #16 needs review.
00:28:52Z no progress: reviewer left pr-16 unchanged
00:28:52Z done reviewer pr-16 exit=3 secs=20 model=gemini-2.5-flash in=24771 out=2311
00:28:56Z wake reviewer pr-16: PR #16 needs review. (retry on backup model gemini gemini-3.5-flash-lite)
00:29:28Z done reviewer pr-16 exit=0 secs=32 model=gemini-3.5-flash-lite in=80075 out=831
```

## 8. Reproduce

Needs: an AWS account on the Free plan (an IAM user with `AmazonEC2FullAccess`, saved as the CLI profile `confer`),
Terraform 1.6+, an SSH key at `~/.ssh/confer_vm`, `gh` signed in as the repo's admin, a DuckDNS domain, and a
`secrets/` folder next to the repo with three key files (never committed):

| File | Keys |
|---|---|
| `agents.env` | `OLLAMA_API_KEY` (and `OLLAMA_API_KEY_2`, optional), `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GH_TOKEN` (the bot account), `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` |
| `app.env` | `GEMINI_API_KEY` (not used by v1) |
| `root.env` | `DUCKDNS_DOMAIN`, `DUCKDNS_TOKEN` |

Every Terraform variable has a default (`infra/variables.tf`). A fork changes `repo_url` and `duckdns_domain`
there, and the repo name in `ops/*.sh` and `agents/setup.sh`.

```
cd infra
terraform init && terraform apply                  # the VM, network and first-boot setup
./push-secrets.sh                                  # keys, DuckDNS, the bot's GitHub login, then ops/setup-vm.sh
cd .. && ops/github-setup.sh                       # labels and the rule that protects main
ssh -i ~/.ssh/confer_vm ubuntu@<vm-ip> 'sudo -u agents rm /home/agents/work/.dispatcher/PAUSE'   # start the team
```

Then file one issue, "Plan the build from SPEC.md", with the label `assign:orchestrator`. The team builds from
there; merge each PR that reaches `ready-to-merge`. On every new commit to `main`, `ops/deploy.sh` (cron, every
2 minutes) updates the VM's checkout and runs `scripts/install.sh`. Details: `infra/README.md`.
