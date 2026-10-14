#!/usr/bin/env bash

# check-site.sh – validate that a given URL returns HTTP 200.
# Usage: scripts/check-site.sh <URL>
#
# Exits with status 0 if the HTTP response code is 200.
# For any other response (including connection errors) it prints a single
# line describing what was received and exits with a non‑zero status.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 URL"
  exit 1
fi

url="$1"

# Use curl to fetch the HTTP status code silently.
# -s: silent, -o /dev/null: discard body, -w "%{http_code}" prints the code.
status=$(curl -s -o /dev/null -w "%{http_code}" "$url" || echo "curl_error")

if [[ "$status" == "200" ]]; then
  # Success – nothing to output.
  exit 0
else
  if [[ "$status" == "curl_error" ]]; then
    echo "Failed to connect to $url"
  else
    echo "HTTP $status from $url"
  fi
  exit 1
fi

