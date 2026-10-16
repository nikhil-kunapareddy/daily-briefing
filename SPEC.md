# Spec: Daily Briefing

What to build, written by the human. The orchestrator turns it into tickets; the tester and reviewer check work against it.
It says what must be true, not how to code it. This is v1, kept small on purpose; later features are change requests.

## Goal

A small public web app that, once a day, fetches the 5 newest submissions in arXiv's `cs.MA` category (multi-agent
systems), saves every run, and shows what is new since the last run.

- **Source:** the arXiv API, one request per run:
  `https://export.arxiv.org/api/query?search_query=cat:cs.MA&sortBy=submittedDate&sortOrder=descending&max_results=5`
  Sorted by submission date, so a new version of an old paper never takes a new paper's place.
- **Schedule:** every day at 02:00 UTC (after arXiv's evening announcement, with or without US daylight saving).
- **Public page:** one UTC day's papers at a time, starting with today, with arrows to earlier days; the run history
  below. No login.
- **Run now:** a button on the page that starts a run, rate-limited.

**How a run works** (rule numbers in brackets). Both cron jobs are here: the app's 02:00 job, and the deploy check
that installs each merge.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 420}}}%%
flowchart TD
  CR["<b>02:00 UTC</b> · app's crontab<br/>python -m app.job"]
  RN["<b>Run now</b> · POST /run-now<br/>rate limit + new row, one transaction (16)"]
  JR["app.job.run(run_id), inside the request (15)"]
  LK{{"run.lock free? (8)"}}
  RQ["1 arXiv request: the 5 newest submissions,<br/>3 s after the last request, with a User-Agent (9)"]
  OK{{"usable response? (6)"}}
  F(["failed run: nothing marked seen, 0 new (6)"])
  NW["new = stable ID never seen (1–4)<br/>save the papers this run saw (7)"]
  SV(["run saved: time · ✓ or ✗ · trigger · seen · new · error (5)"])
  PG["the page reads it through app/db.py (10–12)"]

  CR --> LK
  RN --> JR --> LK
  LK -->|"yes"| RQ
  LK -.->|"busy: wait for the other run"| LK
  RQ --> OK
  OK -->|"yes"| NW --> SV
  OK -->|"no: error, over 10 s, non-200, unparseable,<br/>no entries, or arXiv's error entry"| F --> SV
  SV --> PG

  subgraph DEP["Every 2 min · ops/deploy.sh, after a merge to main"]
    direction LR
    D1["pull main"] --> D2["scripts/install.sh: back up the database, venv and<br/>dependencies, restart the service, one 02:00 crontab line"]
  end
  PG ~~~ DEP
```

## Stack

- **Python 3.12.** Dependencies pinned with exact versions in `requirements.txt`.
- **Flask** with Jinja templates, rendered on the server, served by **gunicorn**: 2 workers, a 60-second timeout,
  bound to `127.0.0.1:8000`, errors to `/var/log/daily-briefing/web.log`. Caddy already serves
  `https://dailyresearch.duckdns.org` and forwards to it.
- **SQLite** in WAL mode, foreign keys on. Each web request opens its own connection and closes it at the end; every
  connection waits up to 5 seconds when the database is busy (`busy_timeout=5000`). Indexes on a paper's stable ID
  (unique) and a run's start time.
- **No Docker**, no other database, nothing paid. The only outside call is the arXiv API.
- **Where it runs:** on the VM as the Linux user `app`, from the `main` checkout in `/srv/daily-briefing`.
  Data in `/var/lib/daily-briefing/` (it exists, owned by `app`), set by the environment variable `DAILY_BRIEFING_DATA`.
  Tests and test servers always point it at a temporary folder, never at the live one.
- **Install:** `scripts/install.sh` sets everything up; after every merge, `ops/deploy.sh` runs it as `app`.
  - Runs **without sudo**, and is safe to run again and again.
  - **Backs up the database first** (SQLite's online backup) into `/var/lib/daily-briefing/backups/`, newest 10 kept.
  - A virtualenv outside the checkout, in `app`'s home, with the dependencies from `requirements.txt`.
  - A **user systemd service** runs gunicorn and is restarted on each install. Lingering is on for `app`, and
    `XDG_RUNTIME_DIR` is set, so `systemctl --user` works from cron.
  - One line in **`app`'s crontab** runs `python -m app.job` at 02:00 UTC, its output added to
    `/var/log/daily-briefing/job.log`. Re-running the install doesn't add a second line.
- **Tests:** in `tests/`, run by `python -m pytest` from the repo root. CI runs them on every PR. Tests never call
  the real arXiv API: they use `fixtures/arxiv_sample.xml`, a real response to the query above (5 entries) with each
  entry's `<summary>` and `<author>` cut out.

## Rules

**Finding what's new**
1. A paper's **stable ID** is its arXiv ID without the version: `2610.13870v2` → `2610.13870`.
2. A paper is **new** in a run when its stable ID was never seen in an earlier successful run.
   - A new version of a paper already seen (v2 after v1) is **not** new.
   - A later version of a paper never seen before (a v3 seen for the first time) **is** new.
   - On the very first run, everything is new, and the page says "First run: everything is new".
3. This comparison is plain code. No model decides anything.
4. Every paper the query returns counts, including ones cross-listed from another category.

**Saving runs**
5. **Every run is saved**, successful or not: start and finish time (UTC), ✓ or ✗, schedule or Run now, how many
   papers it saw, how many were new, and the error for a failed run.
6. A run **fails** when the request errors or takes over 10 seconds, arXiv answers with a non-200 status, the response
   can't be parsed, it contains **no entries** (arXiv sometimes sends a normal-looking response with no results), or
   its entry is arXiv's error entry (an `<id>` that starts with `https://arxiv.org/api/errors`). A failed run marks
   nothing as seen, and its new count is 0.
7. The database keeps which papers each run saw. **Nothing saved is ever deleted or rewritten**, and schema changes
   only add. Per paper, only: stable ID and version (from `<id>`), the title with line breaks and repeated spaces
   collapsed, the PDF link (`<link title="pdf">`), `<published>` and `<updated>`. Never its abstract or authors.
8. One run at a time: a run holds a lock file in the data folder, and a second run waits for it.

**Being polite to arXiv**
9. One request per run, and at most one request every 3 seconds. Send a `User-Agent` that names the app and links
   to this repo.

**The page** (`GET /`)
10. **One UTC day at a time.** A day's papers are the saved papers whose `<published>` date (UTC) is that day, so the
    date shown is the day the papers were submitted, never the day the app ran. Newest first, as a table:
    **S.No** (1, 2, 3…) · **Title** (plain text) · **DOI** (`10.48550/arXiv.<stable ID>`) · **PDF** (a link to the
    entry's `<link title="pdf">`). Above the table: the day's date between two arrows ("← Wed 14 Oct 2026 →"), then
    "N new papers from this day · Last checked 02:00 UTC" (the latest successful run). `/` is today (UTC), and
    `/?day=YYYY-MM-DD` is any other day. ← goes to the day before and → to the day after, as plain links. On the
    oldest day that has papers, ← is greyed out and does nothing; on today, → is. A day before that or after today,
    or a malformed date, answers 404.
11. **A day with no papers** says so in one friendly line instead of the table, never as an empty table, and its
    arrows stay. Today: "No papers for today yet. arXiv posts new papers each evening; the next check is at 02:00 UTC."
    Any earlier day: "No new papers from Sat 17 Oct 2026." The line under the date then reads only "Last checked … UTC".
12. **Run history** below, the same on every day: the newest 10 runs, newest first, each with its time (UTC), ✓ or ✗,
    schedule or Run now, and the new count ("no new submissions" instead of 0, and "First run: everything is new" on
    the first successful run). A failed row shows its error in short.
13. Readable on a phone; times in UTC and say so. One column, at most 760px wide, the system font, one stylesheet
    served by the app, no JavaScript, no CDN. The look is rules 18–19.
14. The footer says: "Thank you to arXiv for use of its open access interoperability." Nothing suggests arXiv endorses the app.

**Run now** (`POST /run-now`)
15. Runs the job with trigger "manual" (a few seconds) and goes back to the page, which shows the new row.
16. Rate limit: at most **one manual run every 10 minutes**, for everyone together, checked and recorded in one
    database transaction, so two presses at once can't both pass. Pressed too soon, the page says
    "Try again in N min" and no run starts.

**Health** (`GET /healthz`)
17. Answers `200` with JSON when the app can read its database: the latest run's time and status. Otherwise `503`.

**The look** (the human approved this from a mockup; build it from these words, not from any HTML)
18. **Top to bottom**, and nothing else:

    ```
    Daily Briefing                                              [ Run now ]
    New papers in arXiv cs.MA (multi-agent systems),    At most once every 10 minutes
    every day at 02:00 UTC
    ───────────────────────────────────────────────────────────────────────
    ←  Wed 14 Oct 2026  →
    5 new papers from this day · Last checked 02:00 UTC
    ┌──────┬──────────────────────────────────────────┬───────────────────────────┬─────┐
    │ S.NO │ TITLE                                    │ DOI                       │ PDF │
    │ 1    │ The Wealth of Agents: Scaling Agentic AI │ 10.48550/arXiv.2610.20518 │ PDF │
    └──────┴──────────────────────────────────────────┴───────────────────────────┴─────┘
    On a day with no papers (rule 11), in place of the table:
    ▌No papers for today yet. arXiv posts new papers each evening; the next check is at 02:00 UTC.

    Run history
    All times UTC.
    TIME            ✓/✗   TRIGGER    RESULT
    15 Oct, 14:32   ✓     Run now    no new submissions
    …the newest 10…
    ───────────────────────────────────────────────────────────────────────
    Thank you to arXiv for use of its open access interoperability. · Source: the arXiv API · /healthz
    ```
    "Try again in N min" (rule 16) appears as the same kind of note, under the header.
19. **Style.** One accent colour for links and the Run now button. Titles bold; DOIs in a smaller, grey, monospace
    font; table headers small, grey and upper-case. The day's date is bold, with the arrows in the accent colour on
    either side; a greyed-out arrow is plain grey text. The "No papers" and "Try again" lines are a pale yellow
    note with a darker left border. ✓ green, ✗ red. Light or dark from the visitor's system setting.
    **On screens 600px wide or less:** the table's header row hides, and each paper's DOI and PDF link sit on one
    line under its title; each history row becomes a small card (time, ✓ or ✗ and trigger on one line, the result
    under it). Nothing scrolls sideways. Only the states that apply are shown: no example or placeholder sections.

## Code layout

Which files belong to which builder, so they can work at the same time without colliding. The orchestrator splits
the work along these lanes and takes each ticket's "Files likely touched" from this table.

| Path | Lane | What it holds |
|---|---|---|
| `app/__init__.py`, `app/config.py` | builder-ingest | The package, and the one place for settings (data folder, arXiv URL, User-Agent) |
| `app/db.py` | builder-ingest | The schema and **all** SQL, including the functions below |
| `app/arxiv.py`, `app/job.py` | builder-ingest | The request and parsing (rules 6–7); one run (rules 1–9); `python -m app.job` |
| `app/web.py`, `app/templates/`, `app/static/style.css` | builder-web | `create_app()`, the routes `/`, `POST /run-now`, `/healthz`, the page (rules 10–19) |
| `scripts/install.sh` | builder-ops | Install, backup, the service and the crontab line (Stack, Install) |
| `requirements.txt` | builder-ingest | Pinned dependencies. Another lane that needs a package asks in its PR |
| `tests/test_<module>.py` | that module's lane | Unit tests |
| `tests/test_behaviour_*.py`, `fixtures/` | tester | Behaviour tests and the saved arXiv response |

**Touch points between lanes** (fixed here; they don't change unless this spec does):
- The web side reads data **only** through `app/db.py`: `latest_briefing()`, `last_briefing_with_papers()`,
  `latest_runs(limit)`, `health()`, `papers_for_day(day)` (that UTC day's papers, newest first) and
  `oldest_paper_day()` (a date, or `None` when nothing is saved). Each returns plain dicts and lists.
- Run now calls `start_manual_run()` in `app/db.py` (the rate-limit check and the new row, one transaction), which
  returns the run's id or how many minutes to wait, then `app.job.run(run_id)`. The schedule runs `python -m app.job`.
- The job writes **only** through `app/db.py`: `start_run(trigger)`, `save_papers(run_id, papers)`,
  `finish_run(run_id, error=None)`. Tests in any lane fill a test database only through these, never with their own SQL.
- gunicorn serves `app.web:create_app()`. Settings come from `app/config.py` only.

**A sensible order** (the orchestrator decides; Needs go by this):
1. builder-ingest: `config`, `db` with the schema and the functions above, `requirements.txt`.
2. tester: `fixtures/arxiv_sample.xml`. builder-ops: `scripts/install.sh` (needs only the names above).
3. builder-ingest: `arxiv` and `job` (needs 1). builder-web: the page and the history (needs 1).
4. builder-web: Run now, its rate limit, and `/healthz` (needs 3).
5. tester: behaviour tests across the lanes on the fixture: the job twice, the page, Run now (needs 3 and 4).

## Done when

1. `https://dailyresearch.duckdns.org` shows today's page, ← reaches each earlier day (the date between arrows, how
   many are new, and the table of S.No, Title, DOI and PDF, or the one-line message), and the run history, laid out
   and styled as rules 18–19 say, on a desktop and a phone.
2. The history has at least 3 real runs, and one of them found no new submissions (Run now right after another run).
3. Run now adds a row. A second press within 10 minutes shows "Try again in N min" and adds no row.
4. The 02:00 UTC schedule is installed, and `scripts/install.sh` can be run twice in a row without errors or
   duplicates, leaving a database backup each time and never more than 10.
5. `python -m pytest` passes in CI, and the tests cover at least:
   - the same fixture run twice: the first run's history row says "First run: everything is new", the second has 0 new;
   - days: each paper is listed under its `<published>` UTC date; ← and → move one day and are greyed out at the
     oldest day with papers and at today; a day with no papers shows its one line; an out-of-range or malformed day
     answers 404;
   - the version rule: `…v1` then `…v2` is not new, a first-seen `…v3` is new;
   - a failed fetch, an empty response and arXiv's error entry are each saved as a failed run;
   - titles are saved with their whitespace collapsed;
   - the Run now rate limit, including two presses at once;
   - `/healthz`.
