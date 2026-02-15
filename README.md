# 🌐 Claude Web Gateway

> Mac mini에서 돌아가는 Claude CLI(Opus)를 외부 인터넷에서 안전하게 사용하는 웹 인터페이스

## 왜?

Claude CLI는 로컬에서만 쓸 수 있다. 밖에서도 내 Mac mini의 Opus에 질문하고 싶다.  
하지만 보안은 타협 없이.

## 구조

```
브라우저 ──HTTPS──▶ Caddy (TLS + Auth) ──▶ FastAPI (127.0.0.1:8450) ──▶ Claude CLI
```

- **Caddy**: 자동 HTTPS, 토큰 인증, 속도 제한
- **FastAPI**: Claude CLI 실행, WebSocket 스트리밍
- **웹 UI**: 깔끔한 채팅 인터페이스

## 보안

| Layer | 방어 |
|-------|------|
| TLS | 모든 통신 암호화 (Let's Encrypt) |
| Auth | Bearer token 인증 |
| Rate Limit | IP/토큰별 요청 제한 |
| Sandbox | CLI를 격리된 subprocess로 실행 |
| Firewall | 443 포트만 외부 노출 |

## Quick Start

```bash
# 1. 설정
cp .env.example .env
# CLAUDE_WEB_TOKEN 생성/입력

# 2. 실행
make setup
make start

# 3. 접속
# https://your-domain.com
```

## License

MIT
