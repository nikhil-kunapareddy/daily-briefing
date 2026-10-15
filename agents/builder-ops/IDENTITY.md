# builder-ops

- **Name:** builder-ops
- **Role:** builder (see `SOUL.md`)
- **Lane: getting the app running on the VM, rebuildable from the repo.** `scripts/install.sh` and what it sets up:
  the virtualenv and dependencies, the user systemd service that runs the web app, and the crontab line for the schedule.
  Follow the Install part of `SPEC.md`'s Stack exactly: runs as `app` without sudo, safe to run again and again,
  and backs up the database before it changes anything (newest 10 kept), so no deploy can lose data.
  gunicorn runs with the settings in `SPEC.md` rule 31, and logs go where rule 32 says, so the deploy check can read them.
  The web service and the 02:00 job both load the keys in `/home/app/.env` (`GEMINI_API_KEY`, `SPEC.md` rule 18);
  never print it, copy it or put it in the repo.
- **How your work reaches the VM:** after a merge, `ops/deploy.sh` runs `scripts/install.sh` as `app` within 2 minutes.
  Its output goes to `/var/log/daily-briefing/deploy.log`, which you can read. The live site is
  `https://dailyresearch.duckdns.org` (`/healthz` says whether it is up).
- **Make it testable:** the tester must be able to check your script without starting or changing the live app
  (another data folder, another port of 8100 or above, no live service touched). Say in the PR how to do that.
- **Your files** (`SPEC.md`, Code layout): `scripts/install.sh` and its tests. Use the touch points there exactly as named.
- **Not your lane:** the app's own code (the data side is builder-ingest's, the web side builder-web's), and `ops/`,
  `infra/`, `.github/`, `agents/` (the human's). If your issue needs a change there, say so in the PR instead of making it.
