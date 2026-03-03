#!/bin/bash
# Load secrets from passage into env
export GITHUB_CLIENT_ID=$(passage show claude-web/github-client-id)
export GITHUB_CLIENT_SECRET=$(passage show claude-web/github-client-secret)
export JWT_SECRET=$(passage show claude-web/jwt-secret)
export BRAVE_API_KEY=$(passage show claude-web/brave-api-key)

# Non-secret config
export ALLOWED_USERS=ecmoce
export BASE_URL=https://ask.ecmoce.com
export HOST=127.0.0.1
export PORT=8450
export CLAUDE_CMD=/Users/mini/.local/bin/claude
export CLAUDE_MODEL=opus
export CLAUDE_TIMEOUT=600
export PATH=/Users/mini/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin

cd /Users/mini/.openclaw/workspace/claude-web
exec .venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8450
