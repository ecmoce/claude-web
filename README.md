# 🌐 Claude Web Gateway

> Mac mini에서 돌아가는 Claude CLI(Opus)를 외부 인터넷에서 안전하게 사용하는 웹 인터페이스

## 📋 Prerequisites

시스템 요구사항:

- **Python 3.11+** (3.12+ 권장)
- **Claude CLI** 설치 및 로그인 필수
- **GitHub 계정** (OAuth 인증용)
- **도메인 및 DNS** (프로덕션 배포용)
- **macOS/Linux** (launchd/systemd 서비스용)

### Claude CLI 설치 및 설정

```bash
# Claude CLI 설치 (Anthropic 공식)
brew install anthropic/claude/claude

# 또는 pipx로 설치
pipx install claude-cli

# Claude CLI 로그인 (Anthropic API 키 필요)
claude login

# 설치 확인
claude --version
claude models  # 사용 가능한 모델 확인
```

> **중요**: Claude CLI가 제대로 로그인되어 있어야 웹 게이트웨이가 작동합니다.

## 🚀 Quick Start (로컬 실행)

```bash
# 1. 저장소 클론 및 의존성 설치
git clone https://github.com/ecmoce/claude-web.git
cd claude-web

# 2. Python 가상환경 생성
python -m venv .venv && source .venv/bin/activate

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 환경변수 설정
cp .env.example .env
# .env 파일 편집 (아래 참고)

# 5. 개발 서버 실행
DEV_MODE=true python -m uvicorn server.main:app --host 127.0.0.1 --port 8450 --reload

# 6. 브라우저에서 접속
open http://127.0.0.1:8450
```

## ⚙️ 환경변수 설정

### 필수 설정

`.env` 파일을 편집하여 다음 값들을 설정:

| 환경변수 | 설명 | 예시 |
|----------|------|------|
| `GITHUB_CLIENT_ID` | GitHub OAuth App ID | `Ov23liABC123def456` |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App Secret | `0123456789abcdef...` |
| `ALLOWED_USERS` | 접근 허용 GitHub 사용자 | `ecmoce,john_doe` |
| `JWT_SECRET` | JWT 토큰 암호화 키 | `a1b2c3d4e5f6...` (32자) |
| `BASE_URL` | 공개 URL (프로덕션) | `https://claude.yourdomain.com` |
| `HOST` | 서버 바인딩 주소 | `127.0.0.1` |
| `PORT` | 서버 포트 | `8450` |

### 선택 설정

| 환경변수 | 설명 | 기본값 |
|----------|------|--------|
| `CLAUDE_CMD` | Claude CLI 명령어 | `claude` |
| `CLAUDE_MODEL` | 사용할 Claude 모델 | `opus` |
| `CLAUDE_TIMEOUT` | Claude 응답 타임아웃(초) | `300` |
| `MAX_CONCURRENT` | 최대 동시 요청 수 | `3` |
| `DEV_MODE` | 개발 모드 활성화 | `false` |
| `SESSION_TTL_HOURS` | 세션 만료 시간 | `24` |

### JWT Secret 생성

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 🔐 GitHub OAuth App 설정

1. **GitHub OAuth App 생성**
   - https://github.com/settings/developers 접속
   - "New OAuth App" 클릭
   - 다음 정보 입력:
     - **Application name**: `Claude Web Gateway`
     - **Homepage URL**: `https://your-domain.com`
     - **Authorization callback URL**: `https://your-domain.com/auth/callback`

2. **앱 정보 복사**
   - Client ID → `.env`의 `GITHUB_CLIENT_ID`
   - Client Secret → `.env`의 `GITHUB_CLIENT_SECRET`

3. **허용 사용자 설정**
   - `.env`의 `ALLOWED_USERS`에 GitHub 사용자명 추가 (소문자, 쉼표 구분)
   - 예: `ALLOWED_USERS=ecmoce,jane_smith,john_doe`

## ☁️ Cloudflare Tunnel 설정 (프로덕션)

### 1. cloudflared 설치

```bash
# macOS
brew install cloudflared

# Linux
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
```

### 2. Tunnel 설정

```bash
# Cloudflare 로그인
cloudflared tunnel login

# Tunnel 생성
cloudflared tunnel create claude-web

# DNS 레코드 연결
cloudflared tunnel route dns claude-web claude.yourdomain.com
```

### 3. config.yml 생성

`~/.cloudflared/config.yml` 파일 생성:

```yaml
tunnel: claude-web
credentials-file: ~/.cloudflared/[TUNNEL-ID].json

ingress:
  - hostname: claude.yourdomain.com
    service: http://127.0.0.1:8450
    originRequest:
      httpHostHeader: claude.yourdomain.com
      
  # 모든 나머지 트래픽 차단
  - service: http_status:404
```

### 4. Tunnel 실행

```bash
# 포그라운드 실행 (테스트)
cloudflared tunnel run claude-web

# 백그라운드 서비스 설치
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
```

## 🖥️ launchd 서비스 등록 (macOS)

### 1. 서비스 파일 생성

`~/Library/LaunchAgents/com.yourdomain.claude-web.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourdomain.claude-web</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/Users/mini/.openclaw/workspace/claude-web/.venv/bin/python</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>server.main:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8450</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>/Users/mini/.openclaw/workspace/claude-web</string>
    
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    
    <key>StandardOutPath</key>
    <string>/tmp/claude-web.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-web.err.log</string>
    
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

### 2. 서비스 등록 및 실행

```bash
# 서비스 로드
launchctl load ~/Library/LaunchAgents/com.yourdomain.claude-web.plist

# 서비스 시작
launchctl start com.yourdomain.claude-web

# 서비스 상태 확인
launchctl list | grep claude-web

# 로그 확인
tail -f /tmp/claude-web.out.log
tail -f /tmp/claude-web.err.log
```

## 📚 API 엔드포인트

### 인증 관련
- `GET /auth/login` - GitHub OAuth 로그인 시작
- `GET /auth/callback` - GitHub OAuth 콜백
- `GET /auth/logout` - 로그아웃
- `GET /api/me` - 현재 사용자 정보

### 채팅 관련
- `POST /api/chat` - Claude와 채팅 (JSON)
- `WebSocket /ws` - 실시간 스트리밍 채팅

### 유틸리티
- `GET /api/health` - 서버 상태 확인
- `GET /api/models` - 사용 가능한 Claude 모델 목록
- `GET /` - 웹 UI (채팅 인터페이스)

### API 사용 예시

```bash
# 건강상태 확인
curl http://localhost:8450/api/health

# 인증된 사용자 정보 (JWT 토큰 필요)
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:8450/api/me

# 채팅 메시지 전송 (JWT 토큰 필요)
curl -X POST http://localhost:8450/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{"message": "안녕하세요, Claude!"}'
```

## 🔒 보안 체크리스트

### 네트워크 보안
- [ ] 방화벽에서 443 포트만 외부 노출
- [ ] Cloudflare Tunnel 사용으로 직접 IP 노출 방지
- [ ] HTTPS 강제 (HTTP → HTTPS 리다이렉트)
- [ ] HSTS 헤더 설정

### 인증 & 인가
- [ ] GitHub OAuth로만 로그인 허용
- [ ] `ALLOWED_USERS` 화이트리스트 엄격 관리
- [ ] JWT 토큰 만료 시간 적절히 설정 (24시간)
- [ ] `JWT_SECRET` 복잡한 랜덤 값 사용

### 환경변수 보안
- [ ] `.env` 파일이 `.gitignore`에 포함됨
- [ ] 프로덕션 환경변수를 Git에 커밋하지 않음
- [ ] API 키들을 안전한 곳에 백업

### 서버 보안
- [ ] `DEV_MODE=false` (프로덕션)
- [ ] 최신 Python/의존성 패키지 사용
- [ ] 로그에 민감한 정보 기록하지 않음
- [ ] Rate limiting 설정 (Cloudflare에서 자동)

### 모니터링
- [ ] 서버 로그 정기 확인
- [ ] 비정상적인 접근 패턴 모니터링
- [ ] Claude CLI 사용량 추적
- [ ] 정기적인 보안 업데이트

## 🛠️ 문제 해결

### Claude CLI 문제
```bash
# Claude CLI 로그인 상태 확인
claude auth status

# 모델 목록 확인
claude models

# 간단한 테스트
claude "안녕하세요"
```

### 서버 시작 문제
```bash
# 포트 충돌 확인
lsof -i :8450

# 환경변수 로딩 확인
python -c "from server.config import settings; print(settings.dict())"

# 의존성 확인
pip list | grep -E "(fastapi|uvicorn|jwt)"
```

### GitHub OAuth 문제
- OAuth App의 콜백 URL이 정확한지 확인
- `GITHUB_CLIENT_ID`와 `GITHUB_CLIENT_SECRET`이 올바른지 확인
- 허용된 사용자 목록(`ALLOWED_USERS`)에 포함되어 있는지 확인

## 🏗️ 아키텍처

```
인터넷 ──HTTPS──▶ Cloudflare Tunnel ──▶ Caddy/Nginx ──▶ FastAPI ──▶ Claude CLI
                        │                     │              │
                        ▼                     ▼              ▼
                   DNS + DDoS          JWT Auth + CORS   subprocess
                   보호 + 캐싱         + Rate Limit      격리 실행
```

## 🎯 사용 사례

### 개발 모드
- 로컬에서 Claude CLI를 웹 인터페이스로 사용
- 팀원들과 같은 네트워크에서 Claude 공유
- 프론트엔드 개발 및 API 테스트

### 프로덕션 모드
- 외부 인터넷에서 안전한 Claude 접근
- 모바일에서 Claude CLI 사용
- 여러 디바이스에서 통일된 Claude 경험

## 📝 라이선스

MIT License

## 🤝 기여

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request