# Daily Briefing

The newest papers in arXiv's **cs.MA** (multi-agent systems) category on one page, refreshed every day at 02:00 UTC,
or on demand with **Run now**.

**Live:** https://dailyresearch.duckdns.org · health check: [`/healthz`](https://dailyresearch.duckdns.org/healthz)

An autonomous team of AI agents wrote and shipped the app; the human files tickets and merges pull requests. No app code is written by hand.

## The team

| Agent | Runtime | Main model | Job |
|---|---|---|---|
| orchestrator | Hermes | `nemotron-3-ultra` | Splits tickets, assigns work |
| builder-ingest | OpenClaw | `gpt-oss:120b` | The arXiv request, the run, the database |
| builder-web | OpenClaw | `gpt-oss:120b` | The page, Run now, `/healthz` |
| builder-ops | OpenClaw | `gpt-oss:120b` | `scripts/install.sh`: the app on the VM |
| tester | OpenClaw | `gemma4:31b` | Tests each PR, sends it back with evidence |
| reviewer | Hermes | `gemini-2.5-flash` | Reviews each tested PR against its ticket and `SPEC.md` |

A plain-code dispatcher (`ops/dispatcher.sh`, every 2 minutes) wakes the agent each ticket needs next.
Backup models, rules and the ticket path: [`agents/WORKFLOW.md`](agents/WORKFLOW.md).

Thank you to arXiv for use of its open access interoperability.
