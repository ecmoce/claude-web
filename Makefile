.PHONY: setup dev start stop logs token clean

# 초기 설정
setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	@echo "✅ Setup 완료. 'make dev'로 개발 서버 시작"

# 개발 모드 (DEV_MODE=true, 인증 스킵)
dev:
	DEV_MODE=true HOST=127.0.0.1 PORT=8450 \
	python3 -m uvicorn server.main:app --reload --host 127.0.0.1 --port 8450

# 프로덕션 시작
start:
	@echo "서버 시작 (백그라운드)..."
	nohup python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8450 > server.log 2>&1 & echo $$! > .pid
	@echo "PID: $$(cat .pid)"

# 서버 중지
stop:
	@if [ -f .pid ]; then \
		kill $$(cat .pid) 2>/dev/null && echo "서버 중지됨" || echo "이미 중지됨"; \
		rm -f .pid; \
	else \
		echo "PID 파일 없음"; \
	fi

# 로그 확인
logs:
	@tail -f server.log

# JWT 토큰 생성
token:
	@python3 -c "import secrets; print(secrets.token_hex(32))"

# 정리
clean:
	rm -rf .venv __pycache__ server/__pycache__ .pid server.log
