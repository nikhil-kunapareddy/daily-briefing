Refs #13

Implements the public page (GET /) with the exact layout and styling from SPEC.md rules 10–14, 18–19.

**What changed:**
- `app/web.py`: Flask app factory `create_app()` with routes for `/`, `POST /run-now`, and `/healthz`. Provides formatted date/time helpers to templates.
- `app/templates/index.html`: Template matching the SPEC.md mockup exactly:
  - Header with "Daily Briefing", subtitle, Run now button with rate limit note
  - Horizontal rule
  - "Try again in N min" note (pale yellow with left border)
  - Latest briefing: date, "N new since the last run" / "First run: everything is new", papers table (S.NO, TITLE, DOI, PDF)
  - No-new case: "No new submissions since the last run (checked HH:MM UTC)" note + previous briefing with its own date
  - Run history: newest 10 runs with TIME, ✓/✗, TRIGGER, RESULT columns
  - Footer with arXiv attribution, source link, /healthz link
- `app/static/style.css`: Complete styling per rules 18–19:
  - One accent colour for links and Run now button
  - Titles bold, DOIs in smaller grey monospace
  - Table headers small, grey, upper-case
  - Pale yellow notes with darker left border for "no new" and "Try again"
  - ✓ green, ✗ red
  - Light/dark from system setting (prefers-color-scheme)
  - Responsive ≤600px: table header hides, DOI+PDF on one line under title, history rows as cards
  - Nothing scrolls sideways, max-width 760px, system font
- `tests/test_web.py`: Comprehensive tests for:
  - Empty state, first run, no-new with previous briefing
  - Run now rate limiting
  - Health endpoint statuses (200/503)
  - History table columns
  - Footer content
  - Responsive data-label attributes
  - Paper title bold, DOI format
  - "no new submissions" text

**Testing:**
All 30 tests pass (including 11 new web tests).

— builder-web · nemotron-3-ultra