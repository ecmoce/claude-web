"""Configuration from environment variables."""
import os

# Server
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8450"))

# GitHub OAuth
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "ecmoce")

# Claude CLI
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "opus")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "3"))
MAX_INPUT_LENGTH = int(os.environ.get("MAX_INPUT_LENGTH", "10000"))

# Session
JWT_SECRET = os.environ.get("JWT_SECRET", "")
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "24"))

# Base URL (for OAuth callback)
BASE_URL = os.environ.get("BASE_URL", "https://localhost:8450")
