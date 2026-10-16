# Token report, per PR

Every agent run from the first commit the agents made on their own (`6a65dbf`, builder-ingest, Oct 14 20:06 UTC,
PR #4 of the dry run) to Oct 16 21:13 UTC: **163 runs and 27 PRs**, all opened by the agents' account.

The numbers are each runtime's own token counts, from the dispatcher's run log on the VM
(`~agents/work/.dispatcher/runs.jsonl`, one line per agent run). Nothing is estimated. "New input" is prompt the model
had to read fresh; "cached" is prompt re-read from the provider's cache; "output" is text the model wrote. Agent time
is how long the runs took, not the wall-clock time a PR was open.

## Where the tokens went

- **105.9M tokens in all.** 99.3% is prompt the models read: instructions, files and command output, re-sent on every
  turn. 91% of that prompt came from the provider's cache. Only 0.7% (741K) is text the models wrote.
- **Most new input went to retries: 5.3M of 9.3M (57%).** Retries are failed runs, fixes after a send-back, and
  repeats of a test or review after a PR changed. They took 85 of the 163 runs. First builds of a ticket took only
  311K new input in all.
- **19 merged PRs took 6.4M new input (69%). 8 PRs closed without merging took 0.9M (10%).** The orchestrator's shared
  board runs took 1.9M (20%). One failed run never became a PR (12K).
- **The four most expensive PRs** are all ones that went round several times:
  - #16, the first database code, took 1.26M in 11 runs. 6 of them failed: 3 hit the night's rate limits and 3 made
    no progress.
  - #53, the day pages, took 1.02M in 9 runs. CI sent it back, the human sent it back, and the page had been rewritten
    to fit old tests.
  - #20, the first page, took 0.87M in 6 runs.
  - #49, the flaky test, took 0.68M in 16 runs, with 3 send-backs by the gates and 1 by the human.
- **`nemotron-3-ultra` read 54% of all new input.** It is the orchestrator's main model and the builders' and tester's
  first backup. It did the builders' big jobs when `gpt-oss:120b` stalled on a fresh ticket: its 10 successful builder
  runs read 2.35M new input, against 0.88M for `gpt-oss:120b`'s 31. 12 of `gpt-oss:120b`'s 43 runs failed: 6 with no
  tool calls, 3 with no progress, 2 errors and 1 rate limit.
- **The reviewer is cheap per run but reads a lot:** 2.1M new input over 36 runs. It re-reads the ticket, `SPEC.md`
  and the diff each time, and writes very little (33K output in all).

## Tokens per PR

Grouped by what the work was for. "Send-backs: gates" counts the dispatcher's log of CI, tester and reviewer
send-backs. "Human" counts send-backs the human made by relabelling a PR and comments posted from the human's account.

| PR | Ticket | Opened by | What | Outcome | Runs (failed) | New input | Cached | Output | Agent time | Send-backs: gates · human |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|
| **Oct 14 dry run (tickets #2, #3)** | | | | | | | | | | |
| #4 | #2 | builder-ingest | Repo hygiene: .gitignore, pytest.ini, test | merged | 3 (0) | 76,667 | 0 | 9,269 | 3 min | 0 · 1 comment |
| #5 | #3 | builder-web | Add site check script and tests | merged | 7 (0) | 174,927 | 0 | 24,598 | 8 min | 0 · – |
| #6 | #– | orchestrator | Collect lessons 2026-10-14 | merged | 0 (0) | 0 | 0 | 0 | 0 s | 0 · – |
| **Oct 16 build (#8, plan from SPEC.md)** | | | | | | | | | | |
| #16 | #9 | builder-ingest | config, db schema & functions, requirements.txt | merged | 11 (6) | 1,260,014 | 5,031,269 | 56,258 | 33 min | 0 · – |
| #17 | #10 | tester | fixtures/arxiv_sample.xml | merged | 3 (0) | 171,332 | 610,634 | 8,298 | 2 min | 0 · – |
| #19 | #12 | builder-ingest | Add arXiv fetch and job runner | merged | 6 (0) | 188,014 | 3,577,594 | 29,885 | 7 min | 1 · – |
| #20 | #13 | builder-web | the page (GET /) with layout and style | merged | 6 (1) | 871,847 | 6,703,814 | 48,852 | 23 min | 0 · – |
| #21 | #11 | builder-ops | add install script | merged | 6 (0) | 137,066 | 2,280,328 | 22,246 | 7 min | 0 · – |
| #22 | #13 | builder-web | Fix health endpoint lazy db import | closed by the human | 4 (0) | 156,151 | 4,984,396 | 30,603 | 6 min | 0 · 1 comment |
| #23 | #13 | builder-web | fix healthz robustness | closed by the human | 1 (0) | 13,205 | 1,181,568 | 9,306 | 1 min | 0 · 1 comment |
| #24 | #11 | builder-ops | Fix install script service ExecStart and add env/working dir | merged | 3 (0) | 107,512 | 1,605,071 | 9,916 | 4 min | 0 · – |
| #25 | #14 | builder-web | Layout tweak: move rate‑limit note out of button container | closed by the human | 3 (0) | 150,742 | 1,665,618 | 11,247 | 4 min | 0 · 1 comment |
| #26 | #14 | builder-web | Run now, rate limit, health endpoint | closed by the human | 2 (0) | 47,321 | 1,813,120 | 9,114 | 3 min | 0 · 1 comment |
| #27 | #15 | tester | behaviour tests across lanes | closed by the human | 2 (0) | 71,197 | 1,002,382 | 9,921 | 2 min | 0 · 1 comment |
| #28 | #15 | tester | Add behaviour tests and real arXiv fixture | merged | 2 (0) | 95,263 | 2,002,503 | 29,515 | 3 min | 0 · – |
| **Oct 16 bug #29 (Run now never fetched)** | | | | | | | | | | |
| #33 | #30 | builder-ingest | Add tests for job.run() with pre-created run_id and latest_runs() in-p… | merged | 7 (5) | 255,587 | 3,303,911 | 22,394 | 14 min | 0 · – |
| #36 | #31 | builder-web | Fix Run now to invoke job.run with manual run ID | merged | 5 (1) | 201,490 | 5,675,202 | 40,153 | 9 min | 0 · – |
| **Oct 16 bug #37 (Try again page lost the date)** | | | | | | | | | | |
| #39 | #38 | builder-web | Fix rate-limited page to show briefing date and times correctly | merged | 5 (2) | 249,420 | 3,607,387 | 19,610 | 12 min | 0 · – |
| **Oct 16 change request #40 (browse by day) and the bugs it surfaced (#47, #54)** | | | | | | | | | | |
| #45 | #41 | builder-ingest | add papers_for_day(day) and oldest_paper_day() to app/db.py | merged | 6 (1) | 234,686 | 2,569,845 | 22,458 | 9 min | 1 · – |
| #46 | #41 | builder-ingest | Fix: Always show run history even when no successful runs exist (Refs … | closed by its builder | 8 (1) | 387,952 | 10,827,017 | 35,907 | 22 min | 2 · 1 comment |
| #48 | #41 | builder-ingest | Fix config fallback for CI (Issue #41) | closed by the human | 1 (0) | 40,773 | 1,161,952 | 7,650 | 3 min | 0 · 1 comment |
| #49 | #47 | builder-web | Fix test_run_now_creates_run_and_rate_limits to use fixture | merged | 16 (4) | 675,489 | 7,513,954 | 45,136 | 30 min | 3 · 1 send-back, 3 comments |
| #53 | #42 | builder-web | page shows one UTC day with arrows, date param, and 404 boundaries | merged | 9 (1) | 1,024,209 | 13,399,716 | 62,472 | 35 min | 1 · 1 send-back, 2 comments |
| #55 | #42 | builder-web | Fix healthz endpoint per SPEC rule 17 | merged | 6 (1) | 196,827 | 3,806,576 | 24,169 | 11 min | 1 · 1 comment |
| #56 | #54 | builder-web | Fix /healthz endpoint to obey SPEC rule 17 | closed by the human | 3 (1) | 70,645 | 1,845,120 | 19,131 | 6 min | 1 · 1 comment |
| #57 | #44 | tester | behaviour tests for day browsing | merged | 5 (0) | 313,642 | 1,738,281 | 10,315 | 12 min | 1 · – |
| #58 | #43 | builder-web | Fix index template meta line and duplicate last‑checked | merged | 5 (0) | 193,681 | 3,777,801 | 14,714 | 7 min | 0 · – |
| (no PR) | #11 | builder-ops | a failed run that never became a PR | – | 1 (1) | 11,702 | 64 | 260 | 28 s | – |
| (shared) | – | orchestrator | splitting, assigning, approvals and lessons for the whole board | – | 27 (0) | 1,899,916 | 4,208,960 | 107,392 | 65 min | – |
| **All** | | | | | **163 (25)** | **9,277,277** | **95,894,083** | **740,789** | **353 min** | |

Notes on single rows:
- **#6** is the orchestrator's lessons PR from the dry run. The orchestrator runs that wrote it are in the shared row.
- **#33's** branch reused ticket #12's name, so the PR's `Refs #30` line decides its ticket. Its 5 failed runs are
  builder-ingest's attempts on #30 before the PR opened.
- **#46 and #48** were builder-ingest chasing a red `main` that its own code didn't cause. The real cause was a test
  calling the live arXiv API, fixed in #49. #46 closed itself after a wrong review; the human closed #48.
- **#53** includes one version that changed the page to fit the old tests. The gates approved it and the human sent
  it back.
- **#56** duplicated #55's fix but changed no app code; the human closed it.

## Tokens per phase

The brief's four phases, labelled by plain rules in run order (the same rules as `ops/token_report.py`):
- **planning:** every orchestrator run.
- **coding:** a builder's first run on a ticket, or the tester's first run on its own ticket.
- **review/test:** the tester's and reviewer's first run on each PR.
- **retries:** any failed run, a builder fixing a sent-back PR, and any repeat of the above on the same ticket or PR.

| Phase | Runs (failed) | New input | Cached | Output | Agent time |
|---|---:|---:|---:|---:|---:|
| planning | 27 (0) | 1,899,916 | 4,208,960 | 107,392 | 65 min |
| coding | 11 (0) | 310,722 | 6,835,248 | 88,640 | 19 min |
| review/test | 40 (0) | 1,781,283 | 21,647,681 | 89,617 | 55 min |
| retries | 85 (25) | 5,285,356 | 63,202,194 | 455,140 | 213 min |

## Tokens per agent

| Agent | Runs (failed) | New input | Cached | Output | Agent time |
|---|---:|---:|---:|---:|---:|
| builder-web | 34 (7) | 2,373,759 | 36,330,144 | 277,646 | 112 min |
| reviewer | 36 (3) | 2,096,294 | 3,823,226 | 33,333 | 25 min |
| orchestrator | 27 (0) | 1,899,916 | 4,208,960 | 107,392 | 65 min |
| tester | 43 (6) | 1,598,440 | 32,024,905 | 166,379 | 79 min |
| builder-ingest | 19 (8) | 1,225,631 | 17,423,168 | 130,653 | 64 min |
| builder-ops | 4 (1) | 83,237 | 2,083,680 | 25,386 | 6 min |

## Tokens per model

| Model | Runs (failed) | New input | Cached | Output | Agent time |
|---|---:|---:|---:|---:|---:|
| `nemotron-3-ultra` (Ollama Cloud) | 42 (2) | 5,025,709 | 32,493,216 | 272,259 | 193 min |
| `gemini-2.5-flash` (Gemini API) | 36 (5) | 2,024,652 | 3,403,846 | 32,593 | 24 min |
| `gpt-oss:120b` (Ollama Cloud) | 43 (12) | 1,174,033 | 29,827,040 | 287,069 | 74 min |
| `gemma4:31b` (Ollama Cloud) | 37 (3) | 911,845 | 29,709,856 | 147,239 | 57 min |
| `gemini-3.5-flash-lite` (Gemini API) | 2 (0) | 141,038 | 460,125 | 1,629 | 1 min |
| no model logged | 3 (3) | 0 | 0 | 0 | 1 min |

## Tokens per period

| Period | Runs (failed) | New input | Cached | Output | Agent time |
|---|---:|---:|---:|---:|---:|
| Oct 14 dry run | 13 (0) | 469,298 | 0 | 38,481 | 16 min |
| Oct 16 build (00:00–07:00) | 77 (14) | 5,047,509 | 43,285,634 | 398,629 | 165 min |
| Oct 16 change requests (07:00–21:15) | 73 (11) | 3,760,470 | 52,608,449 | 303,679 | 171 min |

Of the 25 failed runs: 10 were rate-limited, 7 made no progress, 6 made no tool calls and 2 ended in an error.
- **The rate-limited ones** paused only that agent.
- **13 of the other 15** were retried on the agent's backup model within 3 minutes.
- **The last 2 needed no retry:** #11 closed 4 seconds after the failure, and the human had closed PR #56.

## How runs are tied to PRs

- **A run on a PR** (the log key `pr-N`) counts for PR N: tests, reviews, and builder fixes after a send-back.
- **A run on a ticket** (`issue-N`) by a builder or the tester counts for the first PR that agent opened for ticket N
  after the run started. That covers the run that opened the PR, plus any failed attempts just before it. A PR's
  ticket comes from its `Refs #N` line, or from its branch name `<agent>/N-…`.
- **Orchestrator runs** (board changes, approvals, lessons) serve many tickets at once. They form the shared row and
  are never added to a PR.
- Every one of the 163 runs is counted exactly once, and the per-PR rows add up to the totals.

## Caveats

- **The dry run has no cached counts:** cached tokens were logged from the build on, so the Oct 14 rows show 0.
- **Hermes logs the model it asked for:** a reviewer or orchestrator run that fell back to another model inside the
  run is counted under the first model.
- **No model logged:** 3 rate-limited runs ended before any model answered.
- **Ollama keys changed during the run:** the Ollama Cloud keys were switched twice when the free allowance ran out
  (`DECISIONS.md` #43, and a third key on Oct 16 at 19:00 UTC). The counts are the same whichever key served them.
- **Per-ticket reports:** the dispatcher also posts each ticket's own token report as a comment when the ticket
  closes (`ops/token_report.py issue N`). Every model call is traced in Langfuse too.
