"""Pydantic 스키마 정의."""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """채팅 요청 모델."""
    message: str = Field(..., min_length=1, max_length=10000, description="사용자 메시지")
    file_ids: list[str] | None = Field(None, description="첨부 파일 ID 목록")
    conversation_id: str | None = Field(None, description="대화 ID")


class ChatResponse(BaseModel):
    """채팅 응답 모델."""
    response: str
    model: str
    elapsed: float  # 소요 시간 (초)


class UserInfo(BaseModel):
    """인증된 사용자 정보."""
    username: str
    authenticated: bool = True


class HealthResponse(BaseModel):
    """헬스체크 응답."""
    status: str = "ok"
    version: str = "0.4.0"
    claude_available: bool = True
