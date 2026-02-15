"""FastAPI 메인 앱 — GitHub OAuth, REST API, WebSocket."""
import os
import time
import json
import secrets
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from server.config import HOST, PORT, BASE_URL
from server.auth import (
    login_url, exchange_code, create_session_token,
    get_current_user, set_session_cookie, clear_session_cookie,
    require_auth, verify_session, COOKIE_NAME,
)
from server.models import ChatRequest, ChatResponse, UserInfo, HealthResponse
from server.rate_limit import check_rate_limit
from server.claude_runner import run_claude, stream_claude

# DEV_MODE — 인증 스킵 (GitHub OAuth Client ID/Secret 없을 때)
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("true", "1", "yes")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Claude Web Gateway", version="0.1.0")

# 정적 파일 서빙 (web/ 디렉토리)
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

# 대화 히스토리 (메모리 — 프로덕션에서는 DB 사용)
_history: dict[str, list[dict]] = {}

# OAuth state 저장
_oauth_states: dict[str, float] = {}


def _get_user(request: Request) -> str | None:
    """DEV_MODE면 'dev-user' 반환, 아니면 쿠키에서 확인."""
    if DEV_MODE:
        return "dev-user"
    return get_current_user(request)


def _require_user(request: Request) -> str:
    """인증된 사용자 반환. 없으면 401."""
    user = _get_user(request)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ── 페이지 라우트 ──────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    """메인 페이지 — web/index.html 반환."""
    html_path = web_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Claude Web Gateway</h1><p>web/index.html not found</p>")


# ── 헬스체크 ──────────────────────────────────────────


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


# ── 인증 라우트 ───────────────────────────────────────


@app.get("/api/me")
async def me(request: Request):
    """현재 로그인 상태 반환."""
    user = _get_user(request)
    if user:
        return {"authenticated": True, "username": user, "dev_mode": DEV_MODE}
    return {"authenticated": False}


@app.get("/auth/login")
async def auth_login():
    """GitHub OAuth 시작."""
    if DEV_MODE:
        # DEV_MODE: 바로 세션 생성
        response = RedirectResponse(url="/")
        token = create_session_token("dev-user")
        set_session_cookie(response, token)
        return response

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()
    redirect_uri = f"{BASE_URL}/auth/callback"
    return RedirectResponse(url=login_url(redirect_uri, state))


@app.get("/auth/callback")
async def auth_callback(code: str = "", state: str = ""):
    """GitHub OAuth 콜백."""
    if DEV_MODE:
        return RedirectResponse(url="/")

    # state 검증
    if state not in _oauth_states:
        return JSONResponse({"error": "Invalid state"}, status_code=400)
    if time.time() - _oauth_states.pop(state) > 600:
        return JSONResponse({"error": "State expired"}, status_code=400)

    redirect_uri = f"{BASE_URL}/auth/callback"
    user_info = await exchange_code(code, redirect_uri)
    if not user_info or "login" not in user_info:
        return JSONResponse({"error": "OAuth failed"}, status_code=400)

    username = user_info["login"]
    token = create_session_token(username)
    response = RedirectResponse(url="/")
    set_session_cookie(response, token)
    return response


@app.get("/auth/logout")
async def auth_logout():
    """로그아웃."""
    response = RedirectResponse(url="/")
    clear_session_cookie(response)
    return response


# ── Chat API (REST) ──────────────────────────────────


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(req: ChatRequest, request: Request):
    """Claude에게 질문 (REST, 비스트리밍)."""
    user = _require_user(request)
    check_rate_limit(request, user)

    start = time.time()
    response_text = await run_claude(req.message)
    elapsed = round(time.time() - start, 2)

    # 히스토리 저장
    _history.setdefault(user, []).append({
        "role": "user", "content": req.message, "ts": start
    })
    _history[user].append({
        "role": "assistant", "content": response_text, "ts": time.time()
    })

    return ChatResponse(
        response=response_text,
        model=f"claude-{os.environ.get('CLAUDE_MODEL', 'opus')}",
        elapsed=elapsed,
    )


# ── History API ──────────────────────────────────────


@app.get("/api/history")
async def api_history(request: Request):
    """대화 히스토리 반환."""
    user = _require_user(request)
    return {"history": _history.get(user, [])}


@app.delete("/api/history")
async def clear_history(request: Request):
    """대화 히스토리 삭제."""
    user = _require_user(request)
    _history.pop(user, None)
    return {"cleared": True}


# ── WebSocket (스트리밍) ─────────────────────────────


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    """WebSocket 기반 스트리밍 채팅."""
    await ws.accept()

    # 인증 확인
    if DEV_MODE:
        username = "dev-user"
    else:
        # 쿠키에서 토큰 추출
        token = ws.cookies.get(COOKIE_NAME)
        username = verify_session(token) if token else None
        if not username:
            await ws.send_json({"type": "error", "content": "Not authenticated"})
            await ws.close(code=4001)
            return

    await ws.send_json({"type": "connected", "username": username})
    logger.info("WebSocket 연결: %s", username)

    try:
        while True:
            data = await ws.receive_json()
            message = data.get("message", "").strip()
            if not message:
                await ws.send_json({"type": "error", "content": "Empty message"})
                continue

            if len(message) > 10000:
                await ws.send_json({"type": "error", "content": "Message too long (max 10000)"})
                continue

            # 히스토리에 사용자 메시지 추가
            _history.setdefault(username, []).append({
                "role": "user", "content": message, "ts": time.time()
            })

            # 스트리밍 시작 알림
            await ws.send_json({"type": "start"})
            start = time.time()

            full_response = []
            async for chunk in stream_claude(message):
                full_response.append(chunk)
                await ws.send_json({"type": "chunk", "content": chunk})

            elapsed = round(time.time() - start, 2)
            complete_text = "".join(full_response)

            # 히스토리에 응답 추가
            _history[username].append({
                "role": "assistant", "content": complete_text, "ts": time.time()
            })

            await ws.send_json({
                "type": "done",
                "elapsed": elapsed,
            })

    except WebSocketDisconnect:
        logger.info("WebSocket 종료: %s", username)
    except Exception as e:
        logger.error("WebSocket 에러: %s", e)
        try:
            await ws.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass


# ── 서버 실행 ────────────────────────────────────────


def main():
    """uvicorn으로 서버 시작."""
    import uvicorn
    logger.info("🚀 Claude Web Gateway 시작 — %s:%s (DEV_MODE=%s)", HOST, PORT, DEV_MODE)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
