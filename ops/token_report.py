#!/usr/bin/env python3
"""Token report from the dispatcher's run log. Plain code, no model: the numbers come from each runtime's own output.

    python3 ops/token_report.py issue N    Markdown report for ticket N: its runs and its PRs' runs
    python3 ops/token_report.py all        Markdown report: per phase, per ticket, per agent, per model, with a plain summary

Reads ~/work/.dispatcher/runs.jsonl and dispatch.log (as the agents user on the VM), plus the PR list from GitHub.
A PR belongs to ticket N when its branch is <agent>/N-<name> (WORKFLOW.md, Conventions).
Orchestrator runs on the whole board ("board", "lessons") serve many tickets, so they are listed as shared, never added to one.
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

REPO = "nikhil-kunapareddy/daily-briefing"
STATE = os.path.expanduser(os.environ.get("STATE", "~/work/.dispatcher"))
RESULTS = {0: "ok", 3: "no progress", 4: "no tool calls", 5: "rate-limited", 124: "timed out", 137: "killed"}


def gh_json(*args):
    return json.loads(subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout)


def load_runs():
    """Every run, with its task. Older runs.jsonl lines have no task: take it from the wake line in dispatch.log."""
    wakes = defaultdict(list)                     # (agent, key) -> [(time, task)]
    try:
        with open(os.path.join(STATE, "dispatch.log")) as f:
            for line in f:
                m = re.match(r"(\S+) wake (\S+) (\S+): (.*)", line.rstrip("\n"))
                if m:
                    wakes[(m[2], m[3])].append((m[1], m[4]))
    except FileNotFoundError:
        pass
    runs = []
    with open(os.path.join(STATE, "runs.jsonl")) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if not r.get("task"):
                earlier = [t for t in wakes[(r["agent"], r["key"])] if t[0] <= r["time"]]
                r["task"] = earlier[-1][1] if earlier else ""
            runs.append(r)
    return runs


def pr_to_issue():
    """PR number -> ticket number, from branch names <agent>/N-<name>."""
    out = {}
    for pr in gh_json("pr", "list", "-R", REPO, "--state", "all", "--limit", "300", "--json", "number,headRefName"):
        m = re.match(r"[^/]+/(\d+)-", pr["headRefName"])
        if m:
            out[pr["number"]] = int(m[1])
    return out


def ticket_of(run, prs):
    kind, _, n = run["key"].partition("-")
    if kind == "issue" and n.isdigit():
        return int(n)
    if kind == "pr" and n.isdigit():
        return prs.get(int(n))
    return None                                   # board, lessons: shared


PHASES = ["planning", "coding", "review/test", "retries"]


def add_phases(runs):
    """Label each run with the brief's phases. Plain rules, in run order:
    planning     the orchestrator (splitting, triage, lessons);
    coding       a builder's first run on an issue; the tester's first run on its own issue;
    review/test  the tester's and reviewer's first run on each PR;
    retries      any failed run, a builder fixing a sent-back PR, and any repeat of the above on the same issue or PR."""
    seen = set()
    for r in runs:
        if r.get("exit", 0) != 0:
            r["phase"] = "retries"
        elif r["agent"] == "orchestrator":
            r["phase"] = "planning"
        elif (r["agent"], r["key"]) in seen:
            r["phase"] = "retries"
        elif r["agent"].startswith("builder-") and r["key"].startswith("pr-"):
            r["phase"] = "retries"
        elif r["key"].startswith("issue-"):
            r["phase"] = "coding"
        else:
            r["phase"] = "review/test"
        seen.add((r["agent"], r["key"]))
    return runs


def summary(runs):
    """Where the tokens went, in plain words, from the numbers (no model)."""
    t = totals(runs)
    if not t["runs"]:
        return []
    prompt = t["input"] + (t["cached"] or 0)
    allt = prompt + t["output"]
    phase = {p: totals([r for r in runs if r["phase"] == p]) for p in PHASES}
    top_phase = max(PHASES, key=lambda p: phase[p]["input"])
    agents = defaultdict(int)
    for r in runs:
        agents[r["agent"]] += r.get("input") or 0
    top_agent = max(agents, key=agents.get)
    fails = [r for r in runs if r.get("exit", 0) != 0]
    pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "–"
    out = [f"- **{t['runs']} runs, {num(allt)} tokens in all.** {pct(prompt, allt)} of them are prompt the model reads "
           f"(instructions, files, command output, re-sent every turn); only {pct(t['output'], allt)} is text it writes.",
           f"- **Most input went to {top_phase}** ({pct(phase[top_phase]['input'], t['input'])} of new input tokens). "
           f"Retries took {pct(phase['retries']['input'], t['input'])}.",
           f"- **The biggest spender is {top_agent}** ({pct(agents[top_agent], t['input'])} of new input tokens)."]
    if t["cached"]:
        out.append(f"- **{pct(t['cached'], prompt)} of the prompt was read from the provider's cache**, which is cheaper and faster.")
    if fails:
        out.append(f"- **{len(fails)} failed run(s)** (errors, timeouts, no tool calls or no progress) cost "
                   f"{num(totals(fails)['input'])} input tokens.")
    return out


def num(x):
    return f"{x:,}" if isinstance(x, int) else "–"


def secs(s):
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def totals(runs):
    t = {"runs": len(runs), "input": 0, "cached": 0, "output": 0, "secs": 0}
    for r in runs:
        for k in ("input", "cached", "output", "secs"):
            if isinstance(r.get(k), int):
                t[k] += r[k]
    if not any(isinstance(r.get("cached"), int) for r in runs):
        t["cached"] = None                        # not recorded for these runs: show "–", not 0
    return t


def run_rows(runs):
    rows = ["| When (UTC) | Agent | Model | On | Result | Input | Cached | Output | Time |",
            "|---|---|---|---|---|---:|---:|---:|---:|"]
    for r in runs:
        when = datetime.fromisoformat(r["time"].replace("Z", "+00:00")).strftime("%b %d %H:%M")
        on = "#" + r["key"].split("-", 1)[1] if "-" in r["key"] else r["key"]
        result = RESULTS.get(r.get("exit"), f"failed ({r.get('exit')})")
        model = r.get("model") or "?"
        rows.append(f"| {when} | {r['agent']} | `{model}` | {on} | {result} | {num(r.get('input'))} | "
                    f"{num(r.get('cached'))} | {num(r.get('output'))} | {secs(r.get('secs', 0))} |")
    t = totals(runs)
    rows.append(f"| **Total** | | | | **{t['runs']} runs** | **{num(t['input'])}** | **{num(t['cached'])}** | "
                f"**{num(t['output'])}** | **{secs(t['secs'])}** |")
    return rows


def group_rows(runs, field, title):
    groups = defaultdict(list)
    for r in runs:
        groups[r.get(field) or "?"].append(r)
    rows = [f"| {title} | Runs | Input | Cached | Output | Time |", "|---|---:|---:|---:|---:|---:|"]
    for name, rs in sorted(groups.items(), key=lambda kv: -totals(kv[1])["input"]):
        t = totals(rs)
        rows.append(f"| `{name}` | {t['runs']} | {num(t['input'])} | {num(t['cached'])} | {num(t['output'])} | {secs(t['secs'])} |")
    return rows


def issue_report(n):
    runs, prs = load_runs(), pr_to_issue()
    mine = [r for r in runs if ticket_of(r, prs) == n]
    shared = [r for r in runs if ticket_of(r, prs) is None and re.search(rf"#{n}\b", r.get("task", ""))]
    out = [f"**Token report for #{n}**, from the dispatcher's run log (each runtime's own token counts; no model wrote this).", ""]
    if mine:
        out += run_rows(mine) + ["", "By model:", ""] + group_rows(mine, "model", "Model")
    else:
        out.append("No agent run was keyed to this ticket or its PRs.")
    if shared:
        t = totals(shared)
        runs_word = "run" if t["runs"] == 1 else "runs"
        out += ["", f"Also, {t['runs']} orchestrator {runs_word} on the whole board looked at this ticket "
                f"({num(t['input'])} input, {num(t['output'])} output tokens). They served several tickets, "
                "so they are not in the total."]
    out += ["", "Input excludes cached tokens (prompt re-read from the provider's cache). \"–\" means not recorded.",
            "", "— dispatcher · no model"]
    return "\n".join(out)


def all_report():
    runs, prs = add_phases(load_runs()), pr_to_issue()
    titles = {i["number"]: i["title"] for i in
              gh_json("issue", "list", "-R", REPO, "--state", "all", "--limit", "300", "--json", "number,title")}
    by_ticket = defaultdict(list)
    shared = []
    for r in runs:
        n = ticket_of(r, prs)
        (by_ticket[n] if n is not None else shared).append(r)
    out = ["## Where the tokens went", ""] + summary(runs)
    out += ["", "## Tokens per phase", "", "| Phase | Runs | Input | Cached | Output | Time |", "|---|---:|---:|---:|---:|---:|"]
    for ph in PHASES:
        t = totals([r for r in runs if r["phase"] == ph])
        out.append(f"| {ph} | {t['runs']} | {num(t['input'])} | {num(t['cached'])} | {num(t['output'])} | {secs(t['secs'])} |")
    out += ["", "Input tokens by agent and phase:", "",
            "| Agent | " + " | ".join(PHASES) + " |", "|---|" + "---:|" * len(PHASES)]
    for agent in sorted({r["agent"] for r in runs}):
        cells = [num(totals([r for r in runs if r["agent"] == agent and r["phase"] == ph])["input"]) for ph in PHASES]
        out.append(f"| {agent} | " + " | ".join(cells) + " |")
    out += ["", "## Tokens per ticket", "",
           "| Ticket | Runs | Input | Cached | Output | Time |", "|---|---:|---:|---:|---:|---:|"]
    for n in sorted(by_ticket):
        t = totals(by_ticket[n])
        out.append(f"| #{n} {titles.get(n, '')} | {t['runs']} | {num(t['input'])} | {num(t['cached'])} | "
                   f"{num(t['output'])} | {secs(t['secs'])} |")
    t = totals(shared)
    out.append(f"| Shared orchestrator runs (board, lessons) | {t['runs']} | {num(t['input'])} | {num(t['cached'])} | "
               f"{num(t['output'])} | {secs(t['secs'])} |")
    t = totals(runs)
    out.append(f"| **All** | **{t['runs']}** | **{num(t['input'])}** | **{num(t['cached'])}** | **{num(t['output'])}** | "
               f"**{secs(t['secs'])}** |")
    out += ["", "## Tokens per agent", ""] + group_rows(runs, "agent", "Agent")
    out += ["", "## Tokens per model", ""] + group_rows(runs, "model", "Model")
    out += ["", "Input excludes cached tokens. \"–\" means not recorded (cached tokens are logged from 2026-10-14 on).",
            "Phases: planning = orchestrator; coding = first build of an issue; review/test = first test and first review of a PR;",
            "retries = failed runs, fixes after a send-back, and repeats of any of these. All numbers come from the runtimes' own counts."]
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "issue" and sys.argv[2].isdigit():
        print(issue_report(int(sys.argv[2])))
    elif sys.argv[1:] == ["all"]:
        print(all_report())
    else:
        sys.exit(__doc__)
