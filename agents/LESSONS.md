# Lessons

Rules learned from rejected PRs. Read before you start. Each line links to the PR it came from.
Curated by the orchestrator, merged by the human. At most 20 lines.

- When using `curl -w "%{http_code}"`, check the exit code of curl itself before trusting the printed status code. (https://github.com/nikhil-kunapareddy/daily-briefing/pull/5#issuecomment-6177891983)