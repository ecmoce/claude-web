"""Configuration from environment variables with validation."""
import os
import logging

logger = logging.getLogger(__name__)

# Development mode check
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("true", "1", "yes")

# Server
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8450"))

# GitHub OAuth
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "ecmoce")

# Validation for production
if not DEV_MODE:
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        logger.warning("GitHub OAuth credentials not configured - authentication will fail")
    if not os.environ.get("JWT_SECRET"):
        raise ValueError("JWT_SECRET is required for production mode")

# Claude CLI
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "opus")
CLAUDE_TIMEOUT = min(max(int(os.environ.get("CLAUDE_TIMEOUT", "300")), 30), 1800)  # 30s-30m
MAX_CONCURRENT = min(max(int(os.environ.get("MAX_CONCURRENT", "3")), 1), 10)  # 1-10
MAX_INPUT_LENGTH = min(max(int(os.environ.get("MAX_INPUT_LENGTH", "10000")), 100), 100000)  # 100-100k

# Session
JWT_SECRET = os.environ.get("JWT_SECRET", "")
SESSION_TTL_HOURS = min(max(int(os.environ.get("SESSION_TTL_HOURS", "24")), 1), 168)  # 1h-7d

# Base URL (for OAuth callback)
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8450" if DEV_MODE else "https://localhost:8450")

# Log configuration summary
logger.info("Configuration loaded: DEV_MODE=%s, HOST=%s, PORT=%s, MODEL=%s", 
           DEV_MODE, HOST, PORT, CLAUDE_MODEL)
