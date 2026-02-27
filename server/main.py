"""FastAPI 메인 앱 — GitHub OAuth, REST API, WebSocket, 파일 업로드, SQLite 저장소."""
import os
import time
import json
import secrets
import logging
import uuid
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import HOST, PORT, BASE_URL, CLAUDE_CMD, MAX_INPUT_LENGTH
from server.auth import (
    login_url, exchange_code, create_session_token,
    get_current_user, set_session_cookie, clear_session_cookie,
    require_auth, verify_session, COOKIE_NAME,
)
from server.models import ChatRequest, ChatResponse, UserInfo, HealthResponse
from server.rate_limit import check_rate_limit
from server.claude_runner import run_claude, stream_claude
from server.web_search import brave_search, format_search_results, deep_research
from server.database import init_db, close_db, save_conversation, update_conversation_title, get_conversations, delete_conversation, delete_all_conversations, save_message, get_messages, save_attachment, get_attachment, search_conversations, save_session_mapping, get_session_mapping

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


app = FastAPI(title="Ask", version="0.3.21", lifespan=lifespan)

# 보안 미들웨어
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    response = await call_next(request)
    # 보안 헤더 추가
    if not DEV_MODE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' fonts.googleapis.com cdnjs.cloudflare.com; font-src 'self' fonts.gstatic.com; img-src 'self' data:; connect-src 'self' wss:;"
    return response

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

# OAuth state 저장 (자동 만료 처리)
_oauth_states: dict[str, float] = {}

def cleanup_expired_states():
    """만료된 OAuth state 정리"""
    current_time = time.time()
    expired = [state for state, timestamp in _oauth_states.items() 
               if current_time - timestamp > 600]  # 10분 후 만료
    for state in expired:
        _oauth_states.pop(state, None)
    if expired:
        logger.info("OAuth state 정리: %d개 만료된 상태 제거", len(expired))


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
    # Claude CLI 가용성 체크
    claude_available = True
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_CMD, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        claude_available = proc.returncode == 0
    except Exception:
        claude_available = False
    
    return HealthResponse(version=app.version, claude_available=claude_available)


@app.get("/api/providers")
async def get_providers():
    """사용 가능한 LLM 프로바이더 및 모델 목록 반환."""
    from server.config import LLM_PROVIDERS
    providers = []
    for pid, p in LLM_PROVIDERS.items():
        providers.append({
            "id": pid,
            "name": p["name"],
            "icon": p["icon"],
            "models": p["models"],
            "default_model": p["default_model"],
            "enabled": p["enabled"],
        })
    return {"providers": providers}


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
    
    # 만료된 상태 정리
    cleanup_expired_states()
    
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
    
    # 더 엄격한 파일 ID 검증
    import re
    if not re.match(r'^[a-f0-9]{12}\.[a-zA-Z0-9]{1,10}$', file_id):
        raise HTTPException(status_code=400, detail="잘못된 파일 ID 형식")
    
    file_path = UPLOAD_DIR / file_id
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    
    # 경로 검증
    try:
        resolved_path = file_path.resolve()
        upload_dir_resolved = UPLOAD_DIR.resolve()
        if not str(resolved_path).startswith(str(upload_dir_resolved)):
            raise HTTPException(status_code=400, detail="접근 권한이 없습니다")
    except Exception:
        raise HTTPException(status_code=400, detail="파일 접근 오류")
    
    # 보안 헤더 추가
    from fastapi.responses import FileResponse
    response = FileResponse(file_path)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


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

# WebSocket 세션별 프로세스 관리
active_processes = {}

async def cleanup_websocket_process(session_key: str):
    """WebSocket 프로세스 안전한 정리"""
    if session_key in active_processes:
        try:
            process = active_processes.pop(session_key)
            if process:
                await process.close()
        except Exception as e:
            logger.warning("WebSocket 프로세스 정리 중 오류: %s", e)

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
    
    # 현재 Claude 프로세스 (권한 요청 처리용)
    current_process = None
    # conversation_id → Claude session_id 매핑 (SQLite 영속 저장)
    
    async def handle_normal_message(data):
        nonlocal current_process
        
        message = data.get("message", "").strip()
        file_ids = data.get("file_ids", [])
        conv_id = data.get("conversation_id")
        web_search_enabled = data.get("web_search", False)
        deep_research_enabled = data.get("deep_research", False)
        model = data.get("model")

        if not message and not file_ids:
            await ws.send_json({"type": "error", "content": "Empty message"})
            return

        if len(message) > MAX_INPUT_LENGTH:
            await ws.send_json({"type": "error", "content": f"Message too long (max {MAX_INPUT_LENGTH})"})
            return

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
        
        # stream-json 모드로 Claude 실행
        from server.claude_runner import ClaudeProcess
        current_process = ClaudeProcess()
        
        # 같은 대화면 이전 Claude 세션 이어가기
        resume_sid = await get_session_mapping(conv_id) if conv_id else None
        
        try:
            await current_process.start(message, file_ids if file_ids else None, 
                                      UPLOAD_DIR, search_context, model, resume_sid)
            
            async for event in current_process.read_output():
                event_type = event.get("type")
                
                if event_type == "system":
                    # 초기화 정보
                    subtype = event.get("subtype")
                    if subtype == "init":
                        session_id = event.get("session_id")
                        # 대화별 Claude 세션 매핑 저장
                        if session_id and conv_id:
                            await save_session_mapping(conv_id, session_id)
                            logger.info("세션 매핑: %s → %s", conv_id, session_id)
                        await ws.send_json({
                            "type": "system_init", 
                            "session_id": session_id,
                            "model": event.get("model"),
                            "tools": event.get("tools", [])
                        })
                
                elif event_type == "assistant":
                    # Assistant 메시지 (텍스트 또는 도구 사용)
                    msg_content = event.get("message", {}).get("content", [])
                    
                    for content_item in msg_content:
                        if content_item.get("type") == "tool_use":
                            # 도구 사용 요청
                            tool_use_id = content_item.get("id")
                            tool_name = content_item.get("name")
                            tool_input = content_item.get("input", {})
                            
                            await ws.send_json({
                                "type": "tool_use",
                                "tool_use_id": tool_use_id,
                                "tool_name": tool_name,
                                "tool_input": tool_input,
                                "description": tool_input.get("description", "")
                            })
                        
                        elif content_item.get("type") == "text":
                            # 일반 텍스트 응답
                            text = content_item.get("text", "")
                            full_response.append(text)
                            await ws.send_json({"type": "chunk", "content": text})
                
                elif event_type == "user":
                    # 도구 결과 (권한 요청 포함 가능)
                    msg_content = event.get("message", {}).get("content", [])
                    
                    for content_item in msg_content:
                        if content_item.get("type") == "tool_result":
                            tool_use_id = content_item.get("tool_use_id")
                            content = content_item.get("content", "")
                            is_error = content_item.get("is_error", False)
                            
                            if is_error and "requested permissions" in content:
                                # 권한 요청
                                await ws.send_json({
                                    "type": "permission_request",
                                    "tool_use_id": tool_use_id,
                                    "content": content
                                })
                            else:
                                # 일반 도구 결과
                                await ws.send_json({
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "content": content,
                                    "is_error": is_error
                                })
                
                elif event_type == "result":
                    # 최종 결과 — don't duplicate if already streamed via chunks
                    result_text = event.get("result", "")
                    permission_denials = event.get("permission_denials", [])
                    
                    await ws.send_json({
                        "type": "final_result",
                        "content": result_text,
                        "permission_denials": permission_denials,
                        "total_cost": event.get("total_cost_usd"),
                        "usage": event.get("usage")
                    })
                    break
                
                elif event_type == "error":
                    await ws.send_json({"type": "error", "content": event.get("content", "Unknown error")})
                    break

        except Exception as e:
            logger.error("Claude stream 에러: %s", e)
            await ws.send_json({"type": "error", "content": str(e)})
        finally:
            if current_process:
                await current_process.close()
            current_process = None

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

    try:
        while True:
            data = await ws.receive_json()
            
            # Ping/pong keepalive
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
                continue
            
            # 권한 응답 처리
            if data.get("type") == "permission_response":
                logger.info("권한 응답 수신: %s", data)
                if current_process:
                    tool_use_id = data.get("tool_use_id")
                    allowed = data.get("allowed", False)
                    logger.info("current_process에 권한 응답 전송: %s -> %s", tool_use_id, allowed)
                    await current_process.send_permission_response(tool_use_id, allowed)
                else:
                    logger.warning("current_process가 None입니다. 권한 응답을 처리할 수 없습니다.")
                continue
            
            # 슬래시 명령어 처리
            if data.get("type") == "slash_command":
                command = data.get("command", "").strip()
                if current_process:
                    slash_message = {
                        "type": "user",
                        "message": {"role": "user", "content": command}
                    }
                    await current_process._write_json(slash_message)
                continue

            # 일반 메시지 처리
            await handle_normal_message(data)

    except WebSocketDisconnect:
        logger.info("WebSocket 종료: %s", username)
    except Exception as e:
        logger.error("WebSocket 에러: %s", e)
        try:
            await ws.send_json({"type": "error", "content": "연결 오류가 발생했습니다"})
        except Exception:
            pass
    finally:
        # 프로세스 안전한 정리
        if current_process:
            try:
                await current_process.close()
            except Exception as e:
                logger.warning("WebSocket 종료 중 프로세스 정리 오류: %s", e)


# ── 서버 실행 ────────────────────────────────────────


def main():
    import uvicorn
    logger.info("🚀 Claude Web Gateway 시작 — %s:%s (DEV_MODE=%s)", HOST, PORT, DEV_MODE)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
