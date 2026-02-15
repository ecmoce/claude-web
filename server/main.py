"""FastAPI 메인 앱 — GitHub OAuth, REST API, WebSocket, 파일 업로드."""
import os
import time
import json
import secrets
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
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

app = FastAPI(title="Claude Web Gateway", version="0.2.0")

# 정적 파일 서빙 (web/ 디렉토리)
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

# ── 파일 업로드 설정 ──────────────────────────────────
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CONVERSATIONS_DIR = Path(__file__).parent.parent / "data" / "conversations"
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".md", ".json", ".csv", ".yaml", ".yml",
    ".html", ".css", ".xml", ".log", ".sh", ".sql", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt", ".toml", ".cfg",
    ".ini", ".env", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# 대화 히스토리 (메모리 + 파일 영속성)
_history: dict[str, list[dict]] = {}

# OAuth state 저장
_oauth_states: dict[str, float] = {}


# ── 대화 영속성 ──────────────────────────────────────


def _conv_path(user: str) -> Path:
    """사용자별 대화 파일 경로 (안전한 파일명)."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in user)
    return CONVERSATIONS_DIR / f"{safe_name}.json"


def _load_history(user: str) -> list[dict]:
    """파일에서 대화 히스토리 로드."""
    if user in _history:
        return _history[user]
    path = _conv_path(user)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _history[user] = data
            return data
        except Exception as e:
            logger.warning("대화 로드 실패 %s: %s", user, e)
    _history[user] = []
    return _history[user]


def _save_history(user: str):
    """대화 히스토리를 파일에 저장."""
    try:
        path = _conv_path(user)
        path.write_text(json.dumps(_history.get(user, []), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("대화 저장 실패 %s: %s", user, e)


def _get_user(request: Request) -> str | None:
    """DEV_MODE면 'dev-user' 반환, 아니면 쿠키에서 확인."""
    if DEV_MODE:
        return "dev-user"
    return get_current_user(request)


def _require_user(request: Request) -> str:
    """인증된 사용자 반환. 없으면 401."""
    user = _get_user(request)
    if not user:
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


# ── 파일 업로드 API ──────────────────────────────────


@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """파일 업로드 — 파일 ID 반환."""
    _require_user(request)

    # 확장자 검증
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일명이 없습니다")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"허용되지 않는 파일 형식: {ext}")

    # 파일 읽기 + 크기 검증
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"파일 크기 초과 (최대 {MAX_FILE_SIZE // 1024 // 1024}MB)")

    # 안전한 파일명 생성
    file_id = f"{uuid.uuid4().hex[:12]}{ext}"
    file_path = UPLOAD_DIR / file_id

    file_path.write_bytes(content)
    logger.info("파일 업로드: %s (%d bytes)", file_id, len(content))

    is_image = ext in IMAGE_EXTENSIONS
    return {
        "file_id": file_id,
        "filename": file.filename,
        "size": len(content),
        "is_image": is_image,
    }


@app.get("/api/uploads/{file_id}")
async def get_upload(file_id: str, request: Request):
    """업로드된 파일 조회 (이미지 미리보기용)."""
    _require_user(request)

    # 경로 traversal 방지
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="잘못된 파일 ID")

    file_path = UPLOAD_DIR / file_id
    if not file_path.exists() or not file_path.resolve().parent == UPLOAD_DIR.resolve():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    return FileResponse(file_path)


# ── Chat API (REST) ──────────────────────────────────


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(req: ChatRequest, request: Request):
    """Claude에게 질문 (REST, 비스트리밍)."""
    user = _require_user(request)
    check_rate_limit(request, user)

    start = time.time()
    response_text = await run_claude(req.message, req.file_ids, UPLOAD_DIR)
    elapsed = round(time.time() - start, 2)

    history = _load_history(user)
    history.append({"role": "user", "content": req.message, "ts": start, "file_ids": req.file_ids})
    history.append({"role": "assistant", "content": response_text, "ts": time.time()})
    _save_history(user)

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
    return {"history": _load_history(user)}


@app.delete("/api/history")
async def clear_history(request: Request):
    """대화 히스토리 삭제."""
    user = _require_user(request)
    _history.pop(user, None)
    path = _conv_path(user)
    if path.exists():
        path.unlink()
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
            file_ids = data.get("file_ids", [])

            if not message and not file_ids:
                await ws.send_json({"type": "error", "content": "Empty message"})
                continue

            if len(message) > 10000:
                await ws.send_json({"type": "error", "content": "Message too long (max 10000)"})
                continue

            # file_ids 검증
            if file_ids:
                validated = []
                for fid in file_ids[:5]:  # 최대 5개
                    if isinstance(fid, str) and "/" not in fid and "\\" not in fid and ".." not in fid:
                        if (UPLOAD_DIR / fid).exists():
                            validated.append(fid)
                file_ids = validated

            # 히스토리에 사용자 메시지 추가
            history = _load_history(username)
            history.append({
                "role": "user", "content": message, "ts": time.time(),
                "file_ids": file_ids if file_ids else None,
            })

            # 스트리밍 시작 알림
            await ws.send_json({"type": "start"})
            start = time.time()

            full_response = []
            async for chunk in stream_claude(message, file_ids if file_ids else None, UPLOAD_DIR):
                full_response.append(chunk)
                await ws.send_json({"type": "chunk", "content": chunk})

            elapsed = round(time.time() - start, 2)
            complete_text = "".join(full_response)

            # 히스토리에 응답 추가
            history.append({
                "role": "assistant", "content": complete_text, "ts": time.time()
            })
            _save_history(username)

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
