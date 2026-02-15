"""FastAPI 메인 앱 — GitHub OAuth, REST API, WebSocket, 파일 업로드, SQLite 저장소."""
import os
import time
import json
import secrets
import logging
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

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
from server.web_search import brave_search, format_search_results, deep_research
from server.database import init_db, close_db, save_conversation, update_conversation_title, get_conversations, delete_conversation, delete_all_conversations, save_message, get_messages, save_attachment, get_attachment, search_conversations

# DEV_MODE — 인증 스킵 (GitHub OAuth Client ID/Secret 없을 때)
DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("true", "1", "yes")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("SQLite DB 초기화 완료")
    yield
    await close_db()


app = FastAPI(title="Claude Web Gateway", version="0.3.0", lifespan=lifespan)

# 정적 파일 서빙 (web/ 디렉토리)
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

# ── 파일 업로드 설정 ──────────────────────────────────
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".md", ".json", ".csv", ".yaml", ".yml",
    ".html", ".css", ".xml", ".log", ".sh", ".sql", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt", ".toml", ".cfg",
    ".ini", ".env", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# OAuth state 저장
_oauth_states: dict[str, float] = {}


def _get_user(request: Request) -> str | None:
    if DEV_MODE:
        return "dev-user"
    return get_current_user(request)


def _require_user(request: Request) -> str:
    user = _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ── 페이지 라우트 ──────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
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
    user = _get_user(request)
    if user:
        return {"authenticated": True, "username": user, "dev_mode": DEV_MODE}
    return {"authenticated": False}


@app.get("/auth/login")
async def auth_login():
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
    response = RedirectResponse(url="/")
    clear_session_cookie(response)
    return response


# ── 파일 업로드 API ──────────────────────────────────


@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    _require_user(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일명이 없습니다")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"허용되지 않는 파일 형식: {ext}")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"파일 크기 초과 (최대 {MAX_FILE_SIZE // 1024 // 1024}MB)")

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
    _require_user(request)
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="잘못된 파일 ID")
    file_path = UPLOAD_DIR / file_id
    if not file_path.exists() or not file_path.resolve().parent == UPLOAD_DIR.resolve():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    return FileResponse(file_path)


# ── Conversations API ────────────────────────────────


@app.get("/api/conversations")
async def api_get_conversations(request: Request):
    user = _require_user(request)
    convs = await get_conversations(user)
    return {"conversations": convs}


@app.get("/api/conversations/{conv_id}/messages")
async def api_get_messages(conv_id: str, request: Request):
    user = _require_user(request)
    msgs = await get_messages(conv_id)
    return {"messages": msgs}


@app.delete("/api/conversations/{conv_id}")
async def api_delete_conversation(conv_id: str, request: Request):
    user = _require_user(request)
    ok = await delete_conversation(conv_id, user)
    if not ok:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    return {"deleted": True}


@app.get("/api/search")
async def api_search(q: str, request: Request):
    user = _require_user(request)
    if not q.strip():
        return {"results": []}
    results = await search_conversations(user, q)
    return {"results": results}


# ── History API (하위 호환) ──────────────────────────


@app.get("/api/history")
async def api_history(request: Request):
    user = _require_user(request)
    convs = await get_conversations(user)
    return {"history": [], "conversations": convs}


@app.delete("/api/history")
async def clear_history(request: Request):
    user = _require_user(request)
    await delete_all_conversations(user)
    return {"cleared": True}


# ── Chat API (REST) ──────────────────────────────────


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(req: ChatRequest, request: Request):
    user = _require_user(request)
    check_rate_limit(request, user)

    start = time.time()
    response_text = await run_claude(req.message, req.file_ids, UPLOAD_DIR)
    elapsed = round(time.time() - start, 2)

    # Save to DB
    conv_id = req.conversation_id or f"c_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    convs = await get_conversations(user)
    if not any(c["id"] == conv_id for c in convs):
        title = req.message[:40] + ("..." if len(req.message) > 40 else "")
        await save_conversation(conv_id, user, title)

    await save_message(conv_id, "user", req.message)
    await save_message(conv_id, "assistant", response_text, elapsed)

    return ChatResponse(
        response=response_text,
        model=f"claude-{os.environ.get('CLAUDE_MODEL', 'opus')}",
        elapsed=elapsed,
    )


# ── WebSocket (스트리밍) ─────────────────────────────


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()

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
            # Ping/pong keepalive
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
                continue

            message = data.get("message", "").strip()
            file_ids = data.get("file_ids", [])
            conv_id = data.get("conversation_id")
            web_search_enabled = data.get("web_search", False)
            deep_research_enabled = data.get("deep_research", False)

            if not message and not file_ids:
                await ws.send_json({"type": "error", "content": "Empty message"})
                continue

            if len(message) > 10000:
                await ws.send_json({"type": "error", "content": "Message too long (max 10000)"})
                continue

            # file_ids 검증
            if file_ids:
                validated = []
                for fid in file_ids[:5]:
                    if isinstance(fid, str) and "/" not in fid and "\\" not in fid and ".." not in fid:
                        if (UPLOAD_DIR / fid).exists():
                            validated.append(fid)
                file_ids = validated

            # Ensure conversation exists in DB
            if not conv_id:
                conv_id = f"c_{int(time.time())}_{uuid.uuid4().hex[:4]}"
            convs = await get_conversations(username)
            if not any(c["id"] == conv_id for c in convs):
                title = message[:40] + ("..." if len(message) > 40 else "")
                await save_conversation(conv_id, username, title)

            # Save user message
            msg_id = await save_message(conv_id, "user", message)

            # Save file attachments to DB
            if file_ids:
                for fid in file_ids:
                    fp = UPLOAD_DIR / fid
                    if fp.exists():
                        file_data = fp.read_bytes() if fp.stat().st_size <= MAX_FILE_SIZE else None
                        await save_attachment(
                            message_id=msg_id, filename=fid,
                            original_name=fid, mime_type=None,
                            size=fp.stat().st_size,
                            data=file_data, file_path=str(fp) if not file_data else None,
                        )

            start = time.time()

            # 웹 검색 / 딥 리서치 처리
            search_context = None
            if deep_research_enabled and message:
                await ws.send_json({"type": "status", "content": "🔬 딥 리서치 진행 중..."})
                search_context = await deep_research(message)
            elif web_search_enabled and message:
                await ws.send_json({"type": "status", "content": "🔍 웹 검색 중..."})
                results = await brave_search(message, count=5)
                search_context = format_search_results(results) if results else None

            await ws.send_json({"type": "start", "conversation_id": conv_id})

            full_response = []
            async for chunk in stream_claude(message, file_ids if file_ids else None, UPLOAD_DIR, search_context):
                full_response.append(chunk)
                await ws.send_json({"type": "chunk", "content": chunk})

            elapsed = round(time.time() - start, 2)
            complete_text = "".join(full_response)

            # Save assistant message
            await save_message(conv_id, "assistant", complete_text, elapsed)

            # Auto-title: update title from first user message
            conv_msgs = await get_messages(conv_id)
            if len(conv_msgs) == 2:  # first exchange
                title = message[:40] + ("..." if len(message) > 40 else "")
                await update_conversation_title(conv_id, title)

            await ws.send_json({"type": "done", "elapsed": elapsed, "conversation_id": conv_id})

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
    import uvicorn
    logger.info("🚀 Claude Web Gateway 시작 — %s:%s (DEV_MODE=%s)", HOST, PORT, DEV_MODE)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
