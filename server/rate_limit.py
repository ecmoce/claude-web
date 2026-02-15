"""Rate limiting — IP당 분당 10회, 토큰당 시간당 60회."""
import time
from collections import defaultdict
from fastapi import Request, HTTPException


class RateLimiter:
    """슬라이딩 윈도우 기반 레이트 리미터."""

    def __init__(self):
        # {key: [timestamp, ...]}
        self._hits: dict[str, list[float]] = defaultdict(list)

    def _clean(self, key: str, window: float):
        """만료된 타임스탬프 제거."""
        now = time.time()
        self._hits[key] = [t for t in self._hits[key] if now - t < window]

    def check(self, key: str, window: float, limit: int):
        """제한 초과 시 HTTPException 발생."""
        self._clean(key, window)
        if len(self._hits[key]) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again later.",
            )
        self._hits[key].append(time.time())


# 싱글턴 인스턴스
_limiter = RateLimiter()

# IP당 분당 10회
IP_WINDOW = 60.0
IP_LIMIT = 10

# 토큰(유저)당 시간당 60회
USER_WINDOW = 3600.0
USER_LIMIT = 60


def check_rate_limit(request: Request, username: str | None = None):
    """IP + 유저 레이트 리밋 체크."""
    client_ip = request.client.host if request.client else "unknown"
    _limiter.check(f"ip:{client_ip}", IP_WINDOW, IP_LIMIT)
    if username:
        _limiter.check(f"user:{username}", USER_WINDOW, USER_LIMIT)
