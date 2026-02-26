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
MAX_INPUT_LENGTH = min(max(int(os.environ.get("MAX_INPUT_LENGTH", "50000")), 100), 100000)  # 100-100k

# Gemini CLI (optional)
GEMINI_CMD = os.environ.get("GEMINI_CMD", "gemini")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

# LLM Providers registry — extend here to add new providers
# Each provider: { cmd, default_model, models: [{id, name, desc}], enabled }
import shutil
LLM_PROVIDERS = {
    "claude": {
        "name": "Claude",
        "cmd": CLAUDE_CMD,
        "default_model": CLAUDE_MODEL,
        "icon": "/static/claude-icon.svg",
        "models": [
            {"id": "opus", "name": "Claude Opus 4.6", "desc": "최고 성능"},
            {"id": "sonnet", "name": "Claude Sonnet 4", "desc": "균형"},
            {"id": "haiku", "name": "Claude Haiku 3.5", "desc": "빠른 응답"},
        ],
        "enabled": bool(shutil.which(CLAUDE_CMD)),
    },
    "gemini": {
        "name": "Gemini",
        "cmd": GEMINI_CMD,
        "default_model": GEMINI_MODEL,
        "icon": "/static/gemini-icon.svg",
        "models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "desc": "추론 강화"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "빠른 응답"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "desc": "경량"},
        ],
        "enabled": bool(shutil.which(GEMINI_CMD)),
    },
}

# Session
JWT_SECRET = os.environ.get("JWT_SECRET", "")
SESSION_TTL_HOURS = min(max(int(os.environ.get("SESSION_TTL_HOURS", "24")), 1), 168)  # 1h-7d

# Base URL (for OAuth callback)
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8450" if DEV_MODE else "https://localhost:8450")

# Log configuration summary
logger.info("Configuration loaded: DEV_MODE=%s, HOST=%s, PORT=%s, MODEL=%s", 
           DEV_MODE, HOST, PORT, CLAUDE_MODEL)
