# Claude Web Gateway

Secure web interface for Claude CLI (Opus) — exposes a minimal HTTPS endpoint on a Mac mini for remote Q&A via browser.

## Overview

| Item | Detail |
|------|--------|
| Goal | 외부 인터넷에서 웹 브라우저로 Claude CLI(Opus)에 질의/응답 |
| Host | Mac mini (macOS, ARM64, 유선 이더넷) |
| Model | Claude Opus 4.6 via `claude` CLI |
| Security | Zero-trust: TLS only, token auth, rate limit, no shell escape |

## Architecture

```
[Browser] ──HTTPS──▶ [Reverse Proxy (Caddy)]
                          │
                     TLS termination
                     Token auth check
                     Rate limiting
                          │
                          ▼
                    [Web Server (FastAPI)]
                     ├─ POST /api/chat     → Claude CLI 실행
                     ├─ GET  /api/history  → 대화 이력
                     ├─ GET  /             → SPA (웹 UI)
                     └─ WebSocket /ws      → 스트리밍 응답
                          │
                          ▼
                    [Claude CLI subprocess]
                     claude --print --model opus
                     stdin/stdout pipe
```

## Security Layers

### 1. Network
- Caddy reverse proxy (자동 HTTPS, Let's Encrypt)
- 포트 443만 외부 노출 (라우터 포트포워딩)
- 내부 서버는 127.0.0.1:8450 바인딩 (외부 직접 접근 불가)

### 2. Authentication (GitHub OAuth)
- GitHub OAuth App 로그인 (Authorization Code Flow)
- 허용 사용자: `ecmoce` 만 접근 가능 (`ALLOWED_USERS` 환경변수)
- 로그인 후 서버 발급 JWT 세션 토큰 (HttpOnly, Secure, SameSite=Strict)
- 미인증 요청 → GitHub 로그인 페이지로 리다이렉트
- 세션 만료: 24시간, 갱신 가능

### 3. Rate Limiting
- IP당 분당 10회 요청 제한
- 토큰당 시간당 60회 제한
- 429 Too Many Requests 응답

### 4. Input Sanitization
- 최대 입력 길이 제한 (10,000자)
- Shell injection 방지 (subprocess with list args, no shell=True)
- Claude CLI 플래그 고정 (사용자가 CLI 옵션 변경 불가)

### 5. Process Isolation
- Claude CLI를 별도 사용자/sandbox로 실행
- 타임아웃 300초 (무한 대기 방지)
- 동시 실행 제한 (max 3 세션)

## Tech Stack

| Component | Choice | Reason |
|-----------|--------|--------|
| Web Framework | FastAPI + uvicorn | async, WebSocket 지원, 경량 |
| Reverse Proxy | Caddy | 자동 HTTPS, 설정 간단 |
| Frontend | Vanilla HTML/CSS/JS | 의존성 최소화 |
| Process | asyncio.subprocess | non-blocking CLI 실행 |
| Auth | GitHub OAuth + JWT | ecmoce만 허용, 세션 관리 |
| DDNS | Cloudflare Tunnel 또는 duckdns | 동적 IP 대응 |

## File Structure

```
claude-web/
├── SKILL.md              # 이 파일
├── README.md             # 프로젝트 설명
├── server/
│   ├── main.py           # FastAPI 앱
│   ├── auth.py           # GitHub OAuth + JWT 세션
│   ├── claude_runner.py  # Claude CLI 실행기
│   ├── rate_limit.py     # 속도 제한
│   ├── config.py         # 설정 (env vars)
│   └── models.py         # Pydantic 스키마
├── web/
│   ├── index.html        # 채팅 UI
│   ├── style.css         # 스타일
│   └── app.js            # 프론트엔드 로직
├── Caddyfile             # 리버스 프록시 설정
├── .env.example          # 환경변수 템플릿
├── requirements.txt      # Python 의존성
├── Makefile              # 빌드/실행 명령
└── docs/
    ├── setup.md          # 설치 가이드
    └── security.md       # 보안 설계 상세
```

## Auth Flow (GitHub OAuth)

```
1. 사용자 → GET /
   └─ 세션 없음 → 302 → GitHub OAuth authorize URL

2. GitHub → 사용자 로그인 + 권한 승인
   └─ 302 → GET /auth/callback?code=xxx

3. 서버 → GitHub API로 code → access_token 교환
   └─ GET https://api.github.com/user (토큰으로 사용자 정보 조회)
   └─ login == "ecmoce" 확인
   └─ ❌ 불일치 → 403 Forbidden ("접근 권한 없음")
   └─ ✅ 일치 → JWT 생성, Set-Cookie (HttpOnly, Secure, SameSite=Strict)
   └─ 302 → /

4. 이후 요청: Cookie의 JWT 검증 → 만료/무효 시 다시 1번
```

### 환경변수
```bash
GITHUB_CLIENT_ID=xxx        # GitHub OAuth App Client ID
GITHUB_CLIENT_SECRET=xxx    # GitHub OAuth App Client Secret
ALLOWED_USERS=ecmoce        # 쉼표 구분, 허용 GitHub 사용자
JWT_SECRET=xxx              # JWT 서명 키 (랜덤 생성)
SESSION_TTL_HOURS=24        # 세션 유효 시간
```

### GitHub OAuth App 설정
- GitHub Settings → Developer settings → OAuth Apps → New
- Homepage URL: `https://your-domain.com`
- Callback URL: `https://your-domain.com/auth/callback`
- Scope: `read:user` (최소 권한)

## API Spec

### POST /api/upload
파일 업로드. `multipart/form-data`로 `file` 필드 전송.

**허용 확장자:** .txt, .py, .js, .ts, .md, .json, .csv, .yaml, .yml, .html, .css, .xml, .log, .sh, .sql, .java, .go, .rs, .c, .cpp, .h, .rb, .php, .swift, .kt, .toml, .cfg, .ini, .env, .pdf, .png, .jpg, .jpeg, .gif, .webp

**최대 크기:** 10MB

```json
// Response
{ "file_id": "abc123def456.py", "filename": "main.py", "size": 1234, "is_image": false }
```

### GET /api/uploads/{file_id}
업로드된 파일 조회 (이미지 미리보기용).

### 저장소 (SQLite)
- **DB 파일:** `data/claude-web.db` (WAL 모드, aiosqlite 비동기)
- **테이블:** conversations, messages, attachments + messages_fts (Full-Text Search)
- 10MB 이하 첨부파일은 BLOB으로 DB에 직접 저장, 초과 시 디스크
- `data/` 디렉토리는 `.gitignore`에 포함

### 대화 API
- `GET /api/conversations` — 대화 목록
- `GET /api/conversations/{id}/messages` — 대화 메시지
- `DELETE /api/conversations/{id}` — 대화 삭제
- `GET /api/search?q=검색어` — FTS 기반 대화 내용 검색
- 웹 UI는 서버 API로 대화 관리 (localStorage 미사용)

### GET /auth/login
GitHub OAuth 로그인 리다이렉트

### GET /auth/callback
OAuth 콜백 — 토큰 교환, 사용자 검증, JWT 발급

### GET /auth/logout
세션 쿠키 삭제, 로그아웃

### POST /api/chat
```json
// Request
{ "message": "질문 내용", "conversation_id": "optional-uuid" }

// Response (streaming via SSE)
{ "id": "uuid", "content": "응답 텍스트", "model": "opus", "tokens": 1234, "done": true }
```

### GET /api/history
```json
// Response
{ "conversations": [{ "id": "uuid", "messages": [...], "created_at": "ISO8601" }] }
```

### WebSocket /ws
```json
// Client → Server
{ "type": "message", "content": "질문" }

// Server → Client (스트리밍)
{ "type": "chunk", "content": "응답 조각..." }
{ "type": "done", "tokens": 1234 }
```

## Deployment Checklist

- [ ] Caddy 설치 및 HTTPS 인증서 설정
- [ ] 라우터에서 443 포트포워딩
- [ ] DDNS 설정 (도메인 연결)
- [ ] 환경변수 설정 (.env)
- [ ] Claude CLI 정상 동작 확인
- [ ] launchd로 서비스 등록
- [ ] 방화벽 규칙 확인 (443만 허용)
- [ ] 로그 모니터링 설정

## Commands

```bash
# 개발
make dev          # 로컬 개발 서버 (8450)

# 프로덕션
make setup        # 의존성 설치 + Caddy 설정
make start        # 서비스 시작
make stop         # 서비스 중지
make token        # 새 인증 토큰 생성
make logs         # 로그 확인
```

## Alternatives Considered

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| Cloudflare Tunnel | 포트포워딩 불필요, DDoS 방어 | CF 의존, 지연 증가 | 선택 가능 옵션 |
| Tailscale | P2P, 설정 간단 | 클라이언트 설치 필요 | 모바일 불편 |
| SSH tunnel | 매우 안전 | 매번 연결 필요 | 불편 |
| 직접 노출 | 간단 | 보안 위험 | ❌ |
