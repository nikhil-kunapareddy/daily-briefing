# Environment for every agent run. Sourced by ops/dispatcher.sh and agents/setup.sh; for a manual run:
#   . /srv/daily-briefing/agents/env.sh
# cron starts with a bare environment, so this sets PATH, loads the keys (~/.env, from infra/push-secrets.sh),
# and derives the variable names the tracing plugins expect.
export PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
set -a
# shellcheck source=/dev/null
. "$HOME/.env"
set +a
export OPENCLAW_NO_AUTO_UPDATE=1

# Langfuse. OpenClaw sends OpenTelemetry with a Basic auth header; Hermes's plugin reads its own names.
LANGFUSE_BASIC_AUTH=$(printf %s "${LANGFUSE_PUBLIC_KEY:-}:${LANGFUSE_SECRET_KEY:-}" | base64 -w0)
export LANGFUSE_BASIC_AUTH
export HERMES_LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY:-}
export HERMES_LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY:-}
export HERMES_LANGFUSE_BASE_URL=${LANGFUSE_HOST:-}
