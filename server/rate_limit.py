"""Rate limiting — IP당 분당 10회, 토큰당 시간당 60회."""
import time
from collections import defaultdict, deque
from fastapi import Request, HTTPException
import logging

logger = logging.getLogger(__name__)

class RateLimiter:
    """슬라이딩 윈도우 기반 레이트 리미터 (deque 최적화)."""

    def __init__(self):
        # {key: deque([timestamp, ...])}
        self._hits: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._last_cleanup = time.time()

    def _clean(self, key: str, window: float):
        """만료된 타임스탬프 제거 (deque의 왼쪽부터)."""
        now = time.time()
        hits = self._hits[key]
        
        # deque의 왼쪽부터 만료된 항목 제거
        while hits and now - hits[0] >= window:
            hits.popleft()

    def _global_cleanup(self):
        """주기적으로 비어있는 키 정리"""
        now = time.time()
        if now - self._last_cleanup > 300:  # 5분마다
            empty_keys = [k for k, v in self._hits.items() if not v]
            for k in empty_keys:
                del self._hits[k]
            if empty_keys:
                logger.info("Rate limiter cleanup: %d empty keys removed", len(empty_keys))
            self._last_cleanup = now

    def check(self, key: str, window: float, limit: int):
        """제한 초과 시 HTTPException 발생."""
        self._clean(key, window)
        
        if len(self._hits[key]) >= limit:
            # 다음 허용 시간 계산
            oldest_hit = self._hits[key][0] if self._hits[key] else time.time()
            retry_after = int(window - (time.time() - oldest_hit)) + 1
            
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again later.",
                headers={"Retry-After": str(retry_after)}
            )
        
        self._hits[key].append(time.time())
        self._global_cleanup()


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
