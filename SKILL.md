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

### 2. Authentication
- Bearer token 인증 (환경변수 `CLAUDE_WEB_TOKEN`)
- 토큰 없는 요청 즉시 거부 (401)
- 선택: TOTP 2FA 추가 가능

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
| Auth | Bearer token + argon2 | 안전한 토큰 해싱 |
| DDNS | Cloudflare Tunnel 또는 duckdns | 동적 IP 대응 |

## File Structure

```
claude-web/
├── SKILL.md              # 이 파일
├── README.md             # 프로젝트 설명
├── server/
│   ├── main.py           # FastAPI 앱
│   ├── auth.py           # 토큰 인증 미들웨어
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

## API Spec

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
