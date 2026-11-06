#!/usr/bin/env bash
# Atlassian leg: register ONE confidential OAuth client with Atlassian (RFC 7591 endpoint) for the U2M Per User connection.
# Writes ATLASSIAN_CLIENT_ID to .lab.env and ATLASSIAN_CLIENT_SECRET to .lab.secrets; never prints the secret. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
uv run python -m infra.atlassian_client
