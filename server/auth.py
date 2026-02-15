"""GitHub OAuth + JWT session authentication."""
import os, time, secrets, httpx, jwt
from fastapi import Request, HTTPException, Response
from fastapi.responses import RedirectResponse

# Config
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
ALLOWED_USERS = set(os.environ.get("ALLOWED_USERS", "ecmoce").split(","))
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
SESSION_TTL = int(os.environ.get("SESSION_TTL_HOURS", "24")) * 3600
COOKIE_NAME = "cw_session"

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def login_url(redirect_uri: str, state: str) -> str:
    """Build GitHub OAuth authorize URL."""
    return (
        f"{GITHUB_AUTH_URL}"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=read:user"
        f"&state={state}"
    )


async def exchange_code(code: str, redirect_uri: str) -> dict | None:
    """Exchange OAuth code for access token, then fetch user info."""
    async with httpx.AsyncClient() as client:
        # Code → token
        resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return None

        # Token → user info
        resp = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return resp.json()


def create_session_token(username: str) -> str:
    """Create a JWT session token."""
    return jwt.encode(
        {"sub": username, "iat": int(time.time()), "exp": int(time.time()) + SESSION_TTL},
        JWT_SECRET,
        algorithm="HS256",
    )


def verify_session(token: str) -> str | None:
    """Verify JWT and return username, or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
        if username and username in ALLOWED_USERS:
            return username
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        pass
    return None


def get_current_user(request: Request) -> str | None:
    """Extract and verify user from session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_session(token)


def set_session_cookie(response: Response, token: str):
    """Set HttpOnly secure session cookie."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=SESSION_TTL,
        path="/",
    )


def clear_session_cookie(response: Response):
    """Clear session cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/")


def require_auth(request: Request) -> str:
    """Middleware-style auth check. Returns username or raises."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
